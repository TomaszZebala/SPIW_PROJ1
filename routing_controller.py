"""
 The script implements a simple controller for a network with 6 hosts and 5 switches.
 The switches are connected in a diamond topology (without vertical links):
    - 3 hosts are connected to the left (s1) and 3 to the right (s5) edge of the diamond.

 The overall operation of the controller is as follows:
    - wait for connection establishment from all switches in function _handle_ConnectionUp; therein, among others,
      start _timer_func() to cyclically chenge routing (see also below)
    - default routing is set in all switches on the reception of packet_in messages form the switch,
    - then the routing for (h1-h4) pair in switch s1 is changed every one second in a round-robin manner to
      load balance the traffic through switches s3, s4, s2. This is done in function _timer_func() that is
      triggered every second by a timer started in _handle_ConnectionUp, lines (around) 203-204
"""

"""
========================================================================
ŁĄCZA:
    -> s1 - s2 (200ms, 200 Mbps)
    -> s1 - s3 (50ms, 50 Mbps)
    -> s1 - s4 (10ms, 10 Mbps)

2 INTENCJE:
    1) h1-h4: maksymalne opoźnienie = 60ms
    2) h2-h5: maksymalne opoźnienie = 15ms
    3) reszta ruchu w sieci

ZASADY:
    -> połączenia intencji mają priotytet - 1) idzie przez s1s3, 2) przez s1s4 - w ten sposób zapewnimy spełnienie priorytetu i nie będziemy kombinować
    -> dobór pozostałych połączeń opiera sie na zasadzie losowania ruletkowego (im większe opóźnienie, tym większa szansa, że się trafi)   
        -> jeżeli, na którymś z łącz s1s3/s1s4 będzie BW równe 70% max BW, to zostaje wyłączone z losowania - zostawiamy miejsce na priorytet
    -> na bieżąco należy wyliczać przepustowości na łączach

CO ZROBIĆ:
    -> funkcje do sprawdzania, czy dany ruch jest priorytetowy
    -> co sekundę obliczany jest bitrate na podstawie port stats
    -> kod w PacketIn do obliczania odpowiedniego wyjścia dla danego strumienia
========================================================================
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.util import dpidToStr
from pox.lib.addresses import IPAddr, EthAddr
from pox.lib.packet.arp import arp
from pox.lib.packet.ethernet import ethernet, ETHER_BROADCAST
from pox.lib.packet.packet_base import packet_base
from pox.lib.packet.packet_utils import *
import pox.lib.packet as pkt
from pox.lib.recoco import Timer
import time
import random

log = core.getLogger()

# initialize global variables
# ================DEKLARACJA BANDWIDTH==================
S1_S2_BW = 1_000_000  
S1_S3_BW = 1_000_000  
S1_S4_BW = 1_000_000  
# ================DEKLARACJA BANDWIDTH==================


# ids of connections to switches
s1_dpid = 0
s2_dpid = 0
s3_dpid = 0
s4_dpid = 0
s5_dpid = 0

# port statistics (number of packets sent or received) received from the switches in current step
s1_p1 = 0  # sent - Tx
s1_p4 = 0  # sent
s1_p5 = 0  # sent
s1_p6 = 0  # sent
s2_p1 = 0  # received - Rx
s3_p1 = 0  # received
s4_p1 = 0  # received

# port statistics (number of packets sent or received) received from the switch in previous step
pre_s1_p1 = 0  # sent
pre_s1_p4 = 0  # sent
pre_s1_p5 = 0  # sent
pre_s1_p6 = 0  # sent
pre_s2_p1 = 0  # received
pre_s3_p1 = 0  # received
pre_s4_p1 = 0  # received

# =================zmienne do obliczania bitrate na łączach s1-s3 i s1-s4===================
pre_s1_s3_bytes = 0
s1_s3_bytes = 0
pre_s1_s4_bytes = 0
s1_s4_bytes = 0
pre_s1_s2_bytes = 0
s1_s2_bytes = 0
bitrate_s1_s3 = 0
bitrate_s1_s4 = 0
bitrate_s1_s2 = 0
# =================zmienne do obliczania bitrate na łączach s1-s3 i s1-s4===================


# variable turn controls the round robin operation (takes value from the set 0,1,2)
turn = 0

# routing in the network changes every "routing_timer" seconds
routing_timer = 1

#======================================================================================
def roulette_pick():
    global bitrate_s1_s3, bitrate_s1_s4
    options = [4, 5, 6]
    weights = [200, 50, 10]
    if bitrate_s1_s3 > 0.7*S1_S3_BW:
        options = [4,6]
        weights = [200,10]
    if bitrate_s1_s4 > 0.7*S1_S4_BW:
        options = [4,5]
        weights = [200,50]
    if (bitrate_s1_s4 > 0.7*S1_S4_BW) and (bitrate_s1_s3 > 0.7*S1_S3_BW):
        options = [4]
        weights = [200]
    	
    return random.choices(options, weights=weights, k=1)[0]
    
    
def is_priority_flow(ip_src, ip_dst):
    if ip_src == "10.0.0.1" and ip_dst == "10.0.0.4":
        return "s1s3"  
    if ip_src == "10.0.0.2" and ip_dst == "10.0.0.5":
        return "s1s4"
    return None
#======================================================================================
    
def getTheTime():  # function to create a timestamp
    flock = time.localtime()
    then = "[%s-%s-%s" % (str(flock.tm_year), str(flock.tm_mon), str(flock.tm_mday))

    if int(flock.tm_hour) < 10:
        hrs = "0%s" % (str(flock.tm_hour))
    else:
        hrs = str(flock.tm_hour)
    if int(flock.tm_min) < 10:
        mins = "0%s" % (str(flock.tm_min))
    else:
        mins = str(flock.tm_min)

    if int(flock.tm_sec) < 10:
        secs = "0%s" % (str(flock.tm_sec))
    else:
        secs = str(flock.tm_sec)

    then += "]%s.%s.%s" % (hrs, mins, secs)
    return then


def _timer_func():
    # this function is called on 1-sec timer expiration and changes the routing
    global s1_dpid, s2_dpid, s3_dpid, s4_dpid, s5_dpid, turn

    if (core.openflow.getConnection(s1_dpid) is None):
        # this return avoids error notifications on stopping the network
        # (when connections disappear and getConnection() objects become Null)
        return

    core.openflow.getConnection(s1_dpid).send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    core.openflow.getConnection(s2_dpid).send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    core.openflow.getConnection(s3_dpid).send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    core.openflow.getConnection(s4_dpid).send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    return


def _handle_portstats_received(event):
    # Handling of port statistics retrieved from switches.
    # Observe the use of port statistics here
    # Note: based on https://github.com/tsartsaris/pythess-SDN/blob/master/pythess.py

    global s1_dpid, s2_dpid, s3_dpid, s4_dpid, s5_dpid
    global s1_p1, s1_p4, s1_p5, s1_p6, s2_p1, s3_p1, s4_p1
    global pre_s1_p1, pre_s1_p4, pre_s1_p5, pre_s1_p6, pre_s2_p1, pre_s3_p1, pre_s4_p1

    global pre_s1_s3_bytes, s1_s3_bytes, pre_s1_s4_bytes, s1_s4_bytes, bitrate_s1_s3, bitrate_s1_s4, s1_s2_bytes, bitrate_s1_s2, pre_s1_s2_bytes

    print("===>Event.stats:")
    print(event.stats)
    print("<===")

    if event.connection.dpid == s1_dpid:  # The DPID of one of the switches involved in the link
        for f in event.stats:
            if int(f.port_no) < 65534:
                if f.port_no == 1:
                    pre_s1_p1 = s1_p1
                    s1_p1 = f.rx_packets
                    # print( "s1_p1->", s1_p1, "TxDrop:", f.tx_dropped,"RxDrop:",f.rx_dropped,"TxErr:",f.tx_errors,"CRC:",f.rx_crc_err,"Coll:",f.collisions,"Tx:",f.tx_packets,"Rx:",f.rx_packets)
                if f.port_no == 4:
                    pre_s1_p4 = s1_p4
                    s1_p4 = f.tx_packets
                    pre_s1_s2_bytes = s1_s2_bytes  # ==============================  obliczanie bitrate
                    s1_s2_bytes = f.tx_bytes       # ==============================  na łączu
                    bitrate_s1_s2 = abs(pre_s1_s2_bytes - s1_s2_bytes) * 8  # ==============================  s1 - s3
                    print(f"===================przepustowosc s1-s2: {bitrate_s1_s2}===================")
                if f.port_no == 5:
                    pre_s1_p5 = s1_p5
                    s1_p5 = f.tx_packets
                    pre_s1_s3_bytes = s1_s3_bytes  # ==============================  obliczanie bitrate
                    s1_s3_bytes = f.tx_bytes       # ==============================  na łączu
                    bitrate_s1_s3 = abs(pre_s1_s3_bytes - s1_s3_bytes) * 8  # ==============================  s1 - s3
                    print(f"===================przepustowosc s1-s3: {bitrate_s1_s3}===================")
                if f.port_no == 6:
                    pre_s1_p6 = s1_p6
                    s1_p6 = f.tx_packets
                    pre_s1_s4_bytes = s1_s4_bytes  # ==============================  obliczanie bitrate
                    s1_s4_bytes = f.tx_bytes       # ==============================  na łączu
                    bitrate_s1_s4 = abs(pre_s1_s4_bytes - s1_s4_bytes) * 8  # ==============================  s1 - s4
                    print(f"===================przepustowosc s1-s4: {bitrate_s1_s4}===================")

    if event.connection.dpid == s2_dpid:
        for f in event.stats:
            if int(f.port_no) < 65534:
                if f.port_no == 1:
                    pre_s2_p1 = s2_p1
                    s2_p1 = f.rx_packets
                    # s2_p1=f.rx_bytes
        print(getTheTime(), "s1_p4(Sent):", (s1_p4 - pre_s1_p4), "s2_p1(Received):", (s2_p1 - pre_s2_p1))

    if event.connection.dpid == s3_dpid:
        for f in event.stats:
            if int(f.port_no) < 65534:
                if f.port_no == 1:
                    pre_s3_p1 = s3_p1
                    s3_p1 = f.rx_packets
        print(getTheTime(), "s1_p5(Sent):", (s1_p5 - pre_s1_p5), "s3_p1(Received):", (s3_p1 - pre_s3_p1))

    if event.connection.dpid == s4_dpid:
        for f in event.stats:
            if int(f.port_no) < 65534:
                if f.port_no == 1:
                    pre_s4_p1 = s4_p1
                    s4_p1 = f.rx_packets
        print(getTheTime(), "s1_p6(Sent):", (s1_p6 - pre_s1_p6), "s4_p1(Received):", (s4_p1 - pre_s4_p1))


def _handle_ConnectionUp(event):
    # waits for connections from the switches, and after connecting all of them it starts a round robin timer for triggering h1-h4 routing changes
    global s1_dpid, s2_dpid, s3_dpid, s4_dpid, s5_dpid
    print("ConnectionUp: ", dpidToStr(event.connection.dpid))

    # remember the connection dpid for the switch
    for m in event.connection.features.ports:
        if m.name == "s1-eth1":
            # s1_dpid: the DPID (datapath ID) of switch s1;
            s1_dpid = event.connection.dpid
            print("s1_dpid=", s1_dpid)
        elif m.name == "s2-eth1":
            s2_dpid = event.connection.dpid
            print("s2_dpid=", s2_dpid)
        elif m.name == "s3-eth1":
            s3_dpid = event.connection.dpid
            print("s3_dpid=", s3_dpid)
        elif m.name == "s4-eth1":
            s4_dpid = event.connection.dpid
            print("s4_dpid=", s4_dpid)
        elif m.name == "s5-eth1":
            s5_dpid = event.connection.dpid
            print("s5_dpid=", s5_dpid)

    # if all switches are connected, start 1-second recurring loop timer for round-robin routing changes;
    # _timer_func is to be called on timer expiration to change the flow entry in s1
    if s1_dpid != 0 and s2_dpid != 0 and s3_dpid != 0 and s4_dpid != 0 and s5_dpid != 0:
        Timer(routing_timer, _timer_func, recurring=True)


def _handle_PacketIn(event):
    global s1_dpid, s2_dpid, s3_dpid, s4_dpid, s5_dpid

    packet = event.parsed
    # print( "_handle_PacketIn is called, packet.type:", packet.type, " event.connection.dpid:", event.connection.dpid)

    # Below, set the default/initial routing rules for all switches and ports.
    # All rules are set up in a given switch on packet_in event received from the switch which means no flow entry has been found in the flow table.
    # This setting up may happen either at the very first pactet being sent or after flow entry expirationn inn the switch

    if event.connection.dpid == s1_dpid:
        a = packet.find(
            'arp')  # If packet object does not encapsulate a packet of the type indicated, find() returns None
        if a and a.protodst == "10.0.0.4":
            msg = of.ofp_packet_out(
                data=event.ofp)  # Create packet_out message; use the incoming packet as the data for the packet out
            msg.actions.append(of.ofp_action_output(port=4))  # Add an action to send to the specified port
            event.connection.send(msg)  # Send message to switch

        if a and a.protodst == "10.0.0.5":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=5))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.6":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=6))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.1":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=1))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.2":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=2))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.3":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=3))
            event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800  # rule for IP packets (x0800)
        msg.match.nw_dst = "10.0.0.1"
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.2"
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.3"
        msg.actions.append(of.ofp_action_output(port=3))
        event.connection.send(msg)
        
	#======================================================================================
        ip = packet.find('ipv4')
	
        if not ip:
             return
	
        ip_src = str(ip.srcip)
        ip_dst = str(ip.dstip)
	
        priority_route = is_priority_flow(ip_src, ip_dst)

        if priority_route == "s1s3":
            out_port = 5  # port s1-s3
        elif priority_route == "s1s4":
             out_port = 6  # port s1-s4
        else:
             out_port = roulette_pick()
	
        msg = of.ofp_flow_mod()
        msg.idle_timeout = 2 
        msg.hard_timeout = 0
        msg.match = of.ofp_match.from_packet(packet, event.port)
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.priority = 100  # możesz ustawić wyższy dla intencji, np. 200
        core.openflow.getConnection(event.dpid).send(msg)

    	# forward pakietu natychmiast
        packet_out = of.ofp_packet_out()
        packet_out.data = event.ofp
        packet_out.actions.append(of.ofp_action_output(port=out_port))
        packet_out.in_port = event.port
        core.openflow.getConnection(event.dpid).send(packet_out)
	#======================================================================================

    elif event.connection.dpid == s2_dpid:
        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 1
        msg.match.dl_type = 0x0806  # rule for ARP packets (x0806)
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 1
        msg.match.dl_type = 0x0800
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 2
        msg.match.dl_type = 0x0806
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 2
        msg.match.dl_type = 0x0800
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

    elif event.connection.dpid == s3_dpid:
        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 1
        msg.match.dl_type = 0x0806
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 1
        msg.match.dl_type = 0x0800
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 2
        msg.match.dl_type = 0x0806
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 2
        msg.match.dl_type = 0x0800
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

    elif event.connection.dpid == s4_dpid:
        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 1
        msg.match.dl_type = 0x0806
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 1
        msg.match.dl_type = 0x0800
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 2
        msg.match.dl_type = 0x0806
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 2
        msg.match.dl_type = 0x0800
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

    elif event.connection.dpid == s5_dpid:
        a = packet.find('arp')
        if a and a.protodst == "10.0.0.4":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=4))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.5":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=5))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.6":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=6))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.1":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=1))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.2":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=2))
            event.connection.send(msg)

        if a and a.protodst == "10.0.0.3":
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=3))
            event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.1"
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.in_port = 6
        msg.actions.append(of.ofp_action_output(port=3))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.1"
        msg.actions.append(of.ofp_action_output(port=1))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.2"
        msg.actions.append(of.ofp_action_output(port=2))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.3"
        msg.actions.append(of.ofp_action_output(port=3))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.4"
        msg.actions.append(of.ofp_action_output(port=4))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.5"
        msg.actions.append(of.ofp_action_output(port=5))
        event.connection.send(msg)

        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 0
        msg.hard_timeout = 0
        msg.match.dl_type = 0x0800
        msg.match.nw_dst = "10.0.0.6"
        msg.actions.append(of.ofp_action_output(port=6))
        event.connection.send(msg)


def launch():
    """
    As usually, launch() is the function called by POX to initialize the
    component indicated by a parameter provided to pox.py (routing_controller.py in
    our case). For more info, see
    http://intronetworks.cs.luc.edu/auxiliary_files/mininet/poxwiki.pdf
    """

    global start_time

    """core is an instance of class POXCore (EventMixin) and it can register objects.
       An object with name xxx can be registered to core instance which makes this
       object become a "component" available as pox.core.core.xxx. For examples, see,
       e.g., https://noxrepo.github.io/pox-doc/html/#the-openflow-nexus-core-openflow """
    core.openflow.addListenerByName("PortStatsReceived",
                                    _handle_portstats_received)  # listen for port stats , https://noxrepo.github.io/pox-doc/html/#statistics-events
    core.openflow.addListenerByName("ConnectionUp",
                                    _handle_ConnectionUp)  # listen for the establishment of a new control channel with a switch, https://noxrepo.github.io/pox-doc/html/#connectionup
    core.openflow.addListenerByName("PacketIn",
                                    _handle_PacketIn)  # listen for the reception of packet_in message from switch, https://noxrepo.github.io/pox-doc/html/#packetin
