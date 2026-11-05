#!/bin/sh
# titan_usb_recon.sh — ricognizione della porta USB "dispositivo" della Titan.
#
# SOLA LETTURA: non cambia niente sulla camera. Raccoglie quello che serve per
# far apparire la Titan come scheda di rete USB verso il tablet (piano in
# docs/USB_TRANSPORT.md): controller USB dispositivo, gadget già configurati,
# supporto del kernel, script L4T, server DHCP disponibili, porte in ascolto.
#
# Gira SULLA CAMERA. Dal Mac:
#   adb push tools/titan_usb_recon.sh /tmp/ && adb shell sh /tmp/titan_usb_recon.sh
#
# Meglio con un cavo USB già collegato dal Mac alla porta USB della Titan che
# si pensa sia quella "dispositivo": il registro mostra se è passata in
# modalità dispositivo.

sec() { echo; echo "=== $1"; }

sec "Sistema"
uname -a
head -1 /etc/nv_tegra_release 2>/dev/null

sec "Controller USB dispositivo (UDC)"
ls /sys/class/udc 2>&1
for u in /sys/class/udc/*; do
    [ -e "$u" ] || continue
    echo "$u: state=$(cat $u/state 2>/dev/null) current_speed=$(cat $u/current_speed 2>/dev/null) maximum_speed=$(cat $u/maximum_speed 2>/dev/null)"
done

sec "Gadget USB configurati (configfs)"
mount | grep configfs
G=/sys/kernel/config/usb_gadget
ls $G 2>&1
for g in $G/*; do
    [ -d "$g" ] || continue
    echo "-- $g"
    echo "   UDC=[$(cat $g/UDC 2>/dev/null)]  (vuoto = gadget non collegato alla porta)"
    echo "   idVendor=$(cat $g/idVendor 2>/dev/null) idProduct=$(cat $g/idProduct 2>/dev/null)"
    echo "   funzioni: $(ls $g/functions 2>/dev/null | tr '\n' ' ')"
    for c in $g/configs/*; do
        [ -d "$c" ] && echo "   config $(basename $c): $(ls $c | tr '\n' ' ')"
    done
    for f in $g/functions/*; do
        [ -f "$f/ifname" ] && echo "   $(basename $f): ifname=$(cat $f/ifname) host_addr=$(cat $f/host_addr 2>/dev/null) dev_addr=$(cat $f/dev_addr 2>/dev/null)"
    done
done

sec "Supporto del kernel (gadget e funzioni di rete)"
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz | grep -E "^CONFIG_USB_(CONFIGFS|F_|LIBCOMPOSITE|GADGET=|ETH|U_ETHER)|^# CONFIG_USB_CONFIGFS_(ECM|NCM|RNDIS|EEM)"
else
    echo "/proc/config.gz assente"
fi
echo "-- moduli caricati:"
lsmod 2>/dev/null | grep -iE "usb_f|libcomposite|g_ether|u_ether" || echo "   (nessuno: funzioni compilate nel kernel o assenti)"
echo "-- moduli disponibili:"
find /lib/modules/$(uname -r) -path "*usb/gadget*" -name "*.ko" 2>/dev/null | head -30

sec "Script L4T per la modalità dispositivo"
D=/opt/nvidia/l4t-usb-device-mode
ls -la $D 2>&1
systemctl is-enabled nv-l4t-usb-device-mode.service 2>&1
systemctl is-active nv-l4t-usb-device-mode.service 2>&1
S=$D/nv-l4t-usb-device-mode-start.sh
[ -f $S ] && grep -nE "mkdir|ln -s|UDC|dhcp|192\.168|host_addr|dev_addr|enable_|functions/" $S | head -60

sec "Chi altro tocca il gadget (Insta360?)"
grep -rlE "usb_gadget|l4t-usb-device-mode|functions/(rndis|ecm|ncm|mass_storage)" \
    /usr/local/bin /home/nvidia/insta360 /etc/systemd /etc/init.d /etc/rc.local 2>/dev/null | head -20

sec "Interfacce di rete coinvolte"
ip -4 -o addr
ip link show usb0 2>&1 | head -2
ip link show usb1 2>/dev/null | head -2
ip link show master l4tbr0 2>/dev/null
echo "MAC di eth0: $(cat /sys/class/net/eth0/address 2>/dev/null)"

sec "Server DHCP"
ps -ef | grep -E "dhcpd|dnsmasq|udhcpd" | grep -v grep || echo "(nessun server DHCP in esecuzione)"
for b in dnsmasq udhcpd dhcpd busybox; do
    p=$(command -v $b 2>/dev/null) && echo "disponibile: $p"
done
command -v busybox >/dev/null 2>&1 && busybox --list 2>/dev/null | grep -E "^udhcpd$" && echo "(udhcpd dentro busybox)"
ls /etc/dhcp 2>/dev/null
[ -f $D/dhcpd.conf ] && { echo "-- $D/dhcpd.conf:"; cat $D/dhcpd.conf; }

sec "Porte in ascolto (OSC 20000, file 8000, adb 5555, ssh 22)"
(netstat -tlnp 2>/dev/null || ss -tlnp 2>/dev/null) | grep -E ":(20000|8000|5555|22) " || echo "(né netstat né ss)"

sec "Registro: porta USB dispositivo"
dmesg | grep -iE "xudc|gadget|rndis|udc|cdc_|otg|charger|vbus" | tail -30

echo
echo "=== fine (niente è stato modificato)"
