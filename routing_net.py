#!/usr/bin/python
 
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.util import dumpNodeConnections
from mininet.log import setLogLevel
from mininet.node import Controller 
from mininet.cli import CLI
from functools import partial
from mininet.node import RemoteController
import os
from time import sleep

# Topology: switches interconnected in diamond topology (3 parallel paths, no cross-links); 3 hosts on each side of the diamond

class MyTopo(Topo):
    "Single switch connected to n hosts."
    """ Note that you can control the numer (index) assigned to the ports in switches - see 
           https://mininet.org/api/classmininet_1_1net_1_1Mininet.html#ae01361739c8c8a4ab26a6bf12517d541
        So, for example, you can set:
           self.addLink(s1, s2, port1=10, port2=20, bw=1, delay='10ms', loss=0, max_queue_size=1000, use_htb=True)
    """
    def __init__(self):
        Topo.__init__(self)
        s1=self.addSwitch('s1')
        s2=self.addSwitch('s2')
        s3=self.addSwitch('s3')
        s4=self.addSwitch('s4')
        s5=self.addSwitch('s5')
        h1=self.addHost('h1')
        h2=self.addHost('h2')
        h3=self.addHost('h3')
        h4=self.addHost('h4')
        h5=self.addHost('h5')
        h6=self.addHost('h6')

        self.addLink(h1, s1, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(h2, s1, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(h3, s1, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s1, s2, bw=1, delay='200ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s1, s3, bw=1, delay='50ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s1, s4, bw=1, delay='10ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s2, s5, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s3, s5, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s4, s5, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s5, h4, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s5, h5, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)
        self.addLink(s5, h6, bw=1, delay='0ms', loss=0, max_queue_size=1000, use_htb=True)

def perfTest():
    "Create network and run simple performance test"
    topo = MyTopo()
    #net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink, controller=POXcontroller1)
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink, controller=partial(RemoteController, ip='127.0.0.1', port=6633))
    net.start()
    
    print("Dumping host connections")
    dumpNodeConnections(net.hosts)
    h1,h2,h3=net.get('h1','h2','h3')
    h4,h5,h6=net.get('h4','h5','h6')
    s1,s2,s3,s4,s5=net.get('s1','s2','s3','s4','s5')
    h1.setMAC("0:0:0:0:0:1")
    h2.setMAC("0:0:0:0:0:2")
    h3.setMAC("0:0:0:0:0:3")
    h4.setMAC("0:0:0:0:0:4")
    h5.setMAC("0:0:0:0:0:5")
    h6.setMAC("0:0:0:0:0:6")
    #CLI(net) # launch simple Mininet CLI terminal window
    
    sleep(5)
    # === TEST 1: Testy INTENCJI (QoS) ===
    # Cel: sprawdzić, czy przepływy zgodne z intencjami (h1 -> h4 oraz h2 -> h5) 
    # są odpowiednio trasowane przez ścieżki o niskim opóźnieniu zgodnie z wymaganiami.
    s2.cmd('tcpdump -l -i s2-eth2 -nn -e > s2-eth2-dump-test_1.txt 2>&1 &')
    s3.cmd('tcpdump -l -i s3-eth2 -nn -e > s3-eth2-dump-test_1.txt 2>&1 &')
    s4.cmd('tcpdump -l -i s4-eth2 -nn -e > s4-eth2-dump-test_1.txt 2>&1 &')
    sleep(5)
    print("\n*** TESTY INTENCJI ***")
    h1.cmdPrint('ping -c 50 -s 1400 10.0.0.4 &')
    h2.cmdPrint('ping -c 50 -s 1400 10.0.0.5 &')
    sleep(60)
    s2.cmd('pkill tcpdump')
    s3.cmd('pkill tcpdump')
    s4.cmd('pkill tcpdump')
    
    
    sleep(5)
    # === TEST 2: Testy NIEZARZĄDZANYCH PRZEPŁYWÓW (load balancing) ===
    # Cel: sprawdzić, czy przepływy niezarządzane (h1, h2, h3 do różnych hostów) 
    # są rozkładane na ścieżki zgodnie z polityką load balancing kontrolera.
    s2.cmd('tcpdump -l -i s2-eth2 -nn -e > s2-eth2-dump-test_2.txt 2>&1 &')
    s3.cmd('tcpdump -l -i s3-eth2 -nn -e > s3-eth2-dump-test_2.txt 2>&1 &')
    s4.cmd('tcpdump -l -i s4-eth2 -nn -e > s4-eth2-dump-test_2.txt 2>&1 &')
    sleep(5)
    print("\n*** TESTY NIEZARZĄDZANYCH PRZEPŁYWÓW ***")
    for x in range(50):
        h1.cmd('ping -c 1 -s 1400 10.0.0.5 &')  # h1 → h5
        h1.cmd('ping -c 1 -s 1400 10.0.0.6 &')  # h1 → h6
        h2.cmd('ping -c 1 -s 1400 10.0.0.6 &')  # h2 → h6
        h2.cmd('ping -c 1 -s 1400 10.0.0.4 &')  # h2 → h4
        h3.cmd('ping -c 1 -s 1400 10.0.0.4 &')  # h3 → h4
        h3.cmd('ping -c 1 -s 1400 10.0.0.5 &')  # h3 → h5
        h3.cmd('ping -c 1 -s 1400 10.0.0.6 &')  # h3 → h6
        sleep(5)
    
    sleep(5)
    s2.cmd('pkill tcpdump')
    s3.cmd('pkill tcpdump')
    s4.cmd('pkill tcpdump')
    
    
    sleep(5)
    # === TEST 3: Przeciążenie trasy h1 -> h6 (s1-s3-s5) i rerouting na s1-s4-s5 ===
    # Cel: symulacja przeciążenia łącza h1 → h6 i sprawdzenie, czy ruch zostanie
    # przekierowany na alternatywną ścieżkę z zachowaniem wymagań QoS.
    s2.cmd('tcpdump -l -i s2-eth2 -nn -e > s2-eth2-dump-test_3.txt 2>&1 &')
    s3.cmd('tcpdump -l -i s3-eth2 -nn -e > s3-eth2-dump-test_3.txt 2>&1 &')
    s4.cmd('tcpdump -l -i s4-eth2 -nn -e > s4-eth2-dump-test_3.txt 2>&1 &')
    print("\n*** TEST: Przeciążenie trasy h1-h6 i sprawdzenie reroutingu na s1-s4-s5 ***")
    sleep(5)
    h1.cmd('ping 10.0.0.4 -i 0.01 -s 1400 -c 25500 &')
    sleep(5)
    for x in range(50):
        h1.cmd('ping -c 1 -s 1400 10.0.0.5 &')  # h1 → h5
        h1.cmd('ping -c 1 -s 1400 10.0.0.6 &')  # h1 → h6
        h2.cmd('ping -c 1 -s 1400 10.0.0.6 &')  # h2 → h6
        h2.cmd('ping -c 1 -s 1400 10.0.0.4 &')  # h2 → h4
        h3.cmd('ping -c 1 -s 1400 10.0.0.4 &')  # h3 → h4
        h3.cmd('ping -c 1 -s 1400 10.0.0.5 &')  # h3 → h5
        h3.cmd('ping -c 1 -s 1400 10.0.0.6 &')  # h3 → h6
        sleep(5)
    sleep(5)
    s2.cmd('pkill tcpdump')
    s3.cmd('pkill tcpdump')
    s4.cmd('pkill tcpdump') 

    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    perfTest()
