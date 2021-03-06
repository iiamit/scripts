#!/usr/bin/python
# Author: Hans Lakhan # @jarsnah12 (twitter)
#######################
# Requirements:
#	boto:		pip install -U boto
#
#######################
# To Do
#	1) Create Debug options
#	2) Save previous iptables config and restore after finishing
#	3) Add support for config?
#	4) Change os.system() to subproccess.Popen to manage STDOUT, STDERR better
#
#######################
import boto.ec2
import os
import argparse
import time
import sys
import subprocess
import fcntl
import struct
import socket
from subprocess import Popen, PIPE, STDOUT

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Check if running as root
if os.geteuid() != 0:
	print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] You need to have root privileges to run this script."
	exit()

# Parse user input
parser = argparse.ArgumentParser()
parser.add_argument("image_id", help="Amazon ami image ID.  Example: ami-d05e75b8")
parser.add_argument("image_type", help="Amazon ami image type Example: t2.micro")
parser.add_argument("num_of_instances", type=int, help="The number of amazon instances you'd like to launch")
parser.add_argument("region", help="Select the region: Example: us-east-1")
parser.add_argument("key_id", help="Amazon Access Key ID")
parser.add_argument("access_key", help="Amazon's Secret Key Access")
args = parser.parse_args()

# Display Warning
print "\n++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
print "+ This script will clear out any existing iptable and routing rules. +"
print "++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
print "[" + bcolors.WARNING + "~" + bcolors.ENDC + "] Would you like to continue y/[N]: "
confirm = raw_input()
if ((confirm != "y") and (confirm != "Y")):
	exit("Yeah you're right its probably better to play it safe.")

# system variables;
homeDir = os.getenv("HOME")
FNULL = open(os.devnull, 'w')

# Get Interface IP
def get_ip_address(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x8915,  # SIOCGIFADDR
        struct.pack('256s', ifname[:15])
    )[20:24])

# Get Default Route
def get_default_gateway_linux():
    # Read the default gateway directly from /proc.
    with open("/proc/net/route") as fh:
        for line in fh:
            fields = line.strip().split()
            if fields[1] != '00000000' or not int(fields[3], 16) & 2:
                continue

            return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))

localIP = get_ip_address('eth0')
defaultgateway = get_default_gateway_linux()

# Initialize connection to EC2
try:
	print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC + "] Connecting to Amazon's EC2..."
	conn = boto.ec2.connect_to_region(region_name=args.region, aws_access_key_id=args.key_id, aws_secret_access_key=args.access_key)
except Exception as e:
	print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] Failed to connect to Amazon EC2 because: %s" % e
	exit()

# Check to see if SSH KeyPair already exists
try:
	kp = conn.get_all_key_pairs(keynames="forProxy")
except:
	# pair does not exist, creating new key pair
	print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC + "] Generating ssh keypairs..."
	keypair = conn.create_key_pair("forProxy")
	keypair.save("%s/.ssh" % homeDir)

# Check to see if a security group already exists, if not create one
try:
	sg = conn.get_all_security_groups(groupnames="forProxy")	
except:
	# Security Group does not exist, creating new group
	print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC + "] Generating Amazon Security Group..."
	sg = conn.create_security_group(name="forProxy", description="Used for Proxy servers")
	try:
		# Adding single ssh rule to allow access
		sg.authorize(ip_protocol="tcp", from_port=22, to_port=22, cidr_ip="0.0.0.0/0")
	except Exception as e:
		print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] Generating Amazon Security Group failed because: %s" % e
		exit()


# Launch Amazon Instances
reservations = conn.run_instances(args.image_id, key_name="forProxy", min_count=args.num_of_instances, max_count=args.num_of_instances, instance_type=args.image_type, security_groups=['forProxy'])
print "[" + bcolors.WARNING + "~" + bcolors.ENDC + "] Starting %s instances, please give about 4 minutes for them to fully boot" % args.num_of_instances

#sleep for 4 minutes while booting images
for i in range(21):
    sys.stdout.write('\r')
    sys.stdout.write("[%-20s] %d%%" % ('='*i, 5*i))
    sys.stdout.flush()
    time.sleep(11.5)

# Add tag name to instance for better management
for instance in reservations.instances:
	instance.add_tag("Name", "forProxy")

# Grab list of public IP's assigned to instances that were launched
allInstances = []
reservations = conn.get_all_instances(filters={"tag:Name" : "forProxy", "instance-state-name" : "running"})
for reservation in reservations:
	for instance in reservation.instances:
		if instance.ip_address not in allInstances:
			if (instance.ip_address):
				allInstances.append(instance.ip_address)

interface = 0
# Create ssh Tunnels for socks proxying
print "\n[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Provisioning Hosts....."
for host in allInstances:
	# Enable Tunneling on the remote host
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -o StrictHostKeyChecking=no ubuntu@%s 'echo PermitTunnel yes | sudo tee -a  /etc/ssh/sshd_config'" % (homeDir, host)
	retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
	if retcode:
		print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to modify remote sshd config." 
	
	# Resarting Service to take new config (you'd think a simple reload would be enough)
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -o StrictHostKeyChecking=no ubuntu@%s 'sudo service ssh restart'" % (homeDir, host)
	retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
        if retcode:
                print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to restart remote sshd service."
	
	# Establish tunnel interface
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -w %s:%s -StrictHostKeyChecking=no root@%s &" % (homeDir, interface, interface, host)
	retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
        if retcode:
                print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to establish ssh tunnel on %." % host
	
	# Provision interface
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -StrictHostKeyChecking=no ubuntu@%s 'sudo ifconfig tun%s 10.%s.254.1 netmask 255.255.255.252'" % (homeDir, host, interface, interface)
        retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
        if retcode:
                print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to provision remote interface on %s." % host
	time.sleep(1)
	
	# Enable forwarding on remote host
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -StrictHostKeyChecking=no ubuntu@%s 'sudo su root -c \"echo 1 > /proc/sys/net/ipv4/ip_forward\"'" % (homeDir, host)
        retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
        if retcode:
                print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to enable remote forwarding on %s." % host
	
	# Provision iptables on remote host
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -StrictHostKeyChecking=no ubuntu@%s 'sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE'" % (homeDir, host)
        retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
        if retcode:
                print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to configure remote iptable rules on %s." % host
	
	# Add return route back to us
	sshcmd = "ssh -i %s/.ssh/forProxy.pem -StrictHostKeyChecking=no ubuntu@%s 'sudo route add %s dev tun%s'" % (homeDir, host, localIP, interface)
        retcode = subprocess.call(sshcmd, shell=True, stdout=FNULL, stderr=subprocess.STDOUT)
        if retcode:
                print "[" + bcolors.FAIL + "!" + bcolors.ENDC + "] ERROR: Failed to configure remote routing table on %s." % host
	
	# Turn up our interface
	os.system("ifconfig tun%s up" % interface)
	
	# Provision interface
	os.system("ifconfig tun%s 10.%s.254.2 netmask 255.255.255.252" % (interface, interface))
	interface = interface +1

# setup local forwarding
os.system("echo 1 > /proc/sys/net/ipv4/ip_forward")

# Create iptables rule
# Flush existing rules
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Building iptables....."
os.system("iptables -t nat -F")
os.system("iptables -t mangle -F")
os.system("iptables -F")

count = args.num_of_instances
interface = 1;
nexthopcmd = "ip route add default scope global "

# Allow connections to RFC1918
os.system("iptables -t nat -I POSTROUTING -d 192.168.0.0/16 -j RETURN")
os.system("iptables -t nat -I POSTROUTING -d 172.16.0.0/16 -j RETURN")
os.system("iptables -t nat -I POSTROUTING -d 10.0.0.0/8 -j RETURN")

for host in allInstances:
	# Allow connections to our proxy servers themselves
	os.system("iptables -t nat -I POSTROUTING -d %s -j RETURN" % host)
	# Nat outbound traffic going through our tunnels
	os.system("iptables -t nat -A POSTROUTING -o tun%s -j MASQUERADE " % (interface-1))
	# Build round robin route table command
	nexthopcmd = nexthopcmd + "nexthop via 10." + str(interface-1) + ".254.1 dev tun" + str(interface -1) + " weight 1 "
	# Add static routes for our SSH tunnels
	os.system("ip route add %s via %s dev eth0" % (host, defaultgateway))
	interface = interface + 1
	count = count - 1

# Remove existing default route
os.system("ip route del default")
# Replace with our own new default route
os.system("%s" % nexthopcmd)

print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Done!"
print "\n+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
print "+ Leave this terminal open and start another to run your commands.  +"
print "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
print "[" + bcolors.WARNING + "~" + bcolors.ENDC +"] Press " + bcolors.BOLD + "Enter" + bcolors.ENDC + " to terminate the script gracefully.", raw_input()

# Time to clean up
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Shutting down..."

# Flush iptables
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Flush iptables...."
os.system("iptables -t nat -F")
os.system("iptables -F")

# Cleaning routes
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Correcting Routes....."
interface = args.num_of_instances
for host in allInstances:
	os.system("route del %s dev eth0" % host)
os.system("ip route del default")
os.system("ip route add default via %s dev eth0" % defaultgateway)

# Terminate instance
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Terminating Instances....."
for reservation in reservations:
	for instance in reservation.instances:
		instance.terminate()

print "[" + bcolors.WARNING + "~" + bcolors.ENDC +"] Pausing for 60 seconds so instances can properly terminate....."
time.sleep(60)

# Remove Security Groups
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Deleting Amazon Security Groups....."
try:
	conn.delete_security_group(name="forProxy")
except Exception as e:
	print "[" + bcolors.FAIL + "!" + bcolors.ENC + "Deletion of security group failed because %s" % e

# Remove Key Pairs
print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Removing SSH keys....."
try:
	conn.delete_key_pair(key_name='forProxy')
except Exception as e:
	print "[" + bcolors.FAIL + "!" + bcolors.ENC + "Deletion of key pair failed because %s" % e

# Remove local files
subprocess.Popen("rm -f %s/.ssh/forProxy.pem" % homeDir, shell=True)

# Remove local routing
os.system("echo 0 > /proc/sys/net/ipv4/ip_forward")

print "[" + bcolors.OKGREEN + "*" + bcolors.ENDC +"] Done!"
