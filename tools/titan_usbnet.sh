#!/bin/sh
# titan_usbnet.sh — rete su USB verso tablet/Mac: PROVA TEMPORANEA.
#
# Gira SULLA CAMERA, come root (adb). Cambia solo la configurazione del gadget
# USB in memoria (configfs): RIAVVIANDO LA CAMERA torna tutto com'era, e
# "titan_usbnet.sh rndis" lo rimette a mano. Nessun file su disco viene toccato.
#
#   ecm     scheda di rete USB standard CDC-ECM (macOS, Linux, Android)
#   ncm     variante CDC-NCM (seconda prova, se il tablet non prende ECM)
#   rndis   ripristina la configurazione NVIDIA originale (RNDIS)
#   stato   mostra cosa c'è adesso sulla porta (default)
#
# Cosa cambia rispetto all'originale NVIDIA (vedi docs/USB_TRANSPORT.md):
#   - RNDIS (formato Windows, che macOS non gestisce) -> ECM o NCM;
#   - MAC lato host NON locale, ricavato dal MAC di eth0 di questa camera: il
#     driver Linux/Android chiama l'interfaccia eth0 invece di usb0 (il
#     servizio Ethernet di Android prende solo eth\d) e il MAC resta lo stesso a
#     ogni avvio (il DHCP ridà lo stesso indirizzo).
# Il DHCP c'è già: il dnsmasq di Insta360 serve 192.168.55.10-30 sul bridge
# l4tbr0 (192.168.55.1), dove questo script aggiunge l'interfaccia.
#
# Dal Mac:
#   adb push tools/titan_usbnet.sh /tmp/ && adb shell sh /tmp/titan_usbnet.sh ecm
# adb deve passare dalla RETE (Wi-Fi o Ethernet), non dal cavo USB che si sta
# riconfigurando: durante il cambio la porta USB si scollega per qualche secondo.

G=${G:-/sys/kernel/config/usb_gadget/l4t}
UDC_NAME=${UDC_NAME:-3550000.xudc}
BR=${BR:-l4tbr0}
CFG=$G/configs/c.1

linked() {
    for l in "$CFG"/*; do
        [ -L "$l" ] && basename "$l"
    done
}

unbind() {
    echo "" > "$G/UDC" 2>/dev/null
    sleep 1
}

bind() {
    echo "$UDC_NAME" > "$G/UDC" || return 1
    sleep 2
}

fail() {
    echo "ERRORE: $1"
    # Mai lasciare la porta senza funzione: se il cambio si è fermato a metà,
    # rimetto RNDIS (l'originale) e ricollego.
    if [ -z "$(linked)" ] && [ -d "$G/functions/rndis.usb0" ]; then
        ln -s "$G/functions/rndis.usb0" "$CFG/" 2>/dev/null
    fi
    [ -n "$(linked)" ] && bind
    echo "Porta ricollegata con: $(linked | tr '\n' ' ')"
    echo "Per tornare all'originale: sh $0 rndis   (oppure riavvia la camera)"
    exit 1
}

make_macs() {
    mac=${ETH_MAC:-$(cat /sys/class/net/eth0/address 2>/dev/null)}
    # shellcheck disable=SC2046
    set -- $(echo "$mac" | tr ':' ' ')
    [ $# -eq 6 ] || fail "MAC di eth0 illeggibile: '$mac'"
    # Lato host: primo byte senza i bit 0x01 (multicast) e 0x02 (locale),
    # ultimo byte diverso da eth0. Lato camera: lo stesso, ma locale.
    HOST_MAC=$(printf "%02x:%s:%s:%s:%s:%02x" $(( 0x$1 & 0xfc )) "$2" "$3" "$4" "$5" $(( 0x$6 ^ 0x01 )))
    DEV_MAC=$(printf "%02x:%s:%s:%s:%s:%s" $(( (0x$1 & 0xfc) | 0x02 )) "$2" "$3" "$4" "$5" "$6")
}

join_bridge() {
    ifc=$(cat "$1/ifname" 2>/dev/null)
    if [ -z "$ifc" ] || [ ! -e "/sys/class/net/$ifc" ]; then
        echo "ATTENZIONE: interfaccia di $(basename "$1") non trovata ('$ifc')"
        return
    fi
    if [ ! -e "/sys/class/net/$BR/brif/$ifc" ]; then
        brctl addif "$BR" "$ifc" || echo "ATTENZIONE: $ifc non aggiunta al bridge $BR"
    fi
    ip link set "$ifc" up
}

switch_to() {
    kind=$1
    F=$G/functions/$kind.usb0
    [ -d "$G" ] || fail "gadget $G assente"
    if linked | grep -qx "$kind.usb0"; then
        echo "La porta è già in modalità $kind."
        show
        return
    fi
    make_macs
    echo "Porta USB -> $kind  (MAC lato host $HOST_MAC, lato camera $DEV_MAC)"
    unbind
    for l in $(linked); do
        rm "$CFG/$l" || fail "non riesco a staccare $l"
    done
    for other in ecm ncm; do
        if [ "$other" != "$kind" ] && [ -d "$G/functions/$other.usb0" ]; then
            rmdir "$G/functions/$other.usb0" || fail "non riesco a togliere $other.usb0"
        fi
    done
    [ -d "$F" ] || mkdir "$F" || fail "il kernel non crea $kind.usb0"
    echo "$HOST_MAC" > "$F/host_addr" || fail "host_addr non impostato"
    echo "$DEV_MAC" > "$F/dev_addr" || fail "dev_addr non impostato"
    ln -s "$F" "$CFG/" || fail "non riesco a collegare $kind.usb0 alla configurazione"
    bind || fail "non riesco a ricollegare la porta"
    join_bridge "$F"
    echo "Fatto. Ricollega o lascia collegato il cavo: l'host deve vedere una scheda di rete."
    show
}

restore() {
    [ -d "$G/functions/rndis.usb0" ] || fail "rndis.usb0 non esiste più: riavvia la camera"
    if [ "$(linked)" = "rndis.usb0" ]; then
        echo "La porta è già nella configurazione originale (RNDIS)."
        show
        return
    fi
    echo "Porta USB -> rndis (configurazione NVIDIA originale)"
    unbind
    for l in $(linked); do
        rm "$CFG/$l" || fail "non riesco a staccare $l"
    done
    for k in ecm ncm; do
        [ -d "$G/functions/$k.usb0" ] && { rmdir "$G/functions/$k.usb0" || fail "non riesco a togliere $k.usb0"; }
    done
    ln -s "$G/functions/rndis.usb0" "$CFG/" || fail "non riesco a ricollegare rndis.usb0"
    bind || fail "non riesco a ricollegare la porta"
    join_bridge "$G/functions/rndis.usb0"
    show
}

show() {
    echo
    echo "== Porta USB"
    echo "   collegata a: [$(cat "$G/UDC" 2>/dev/null)]  stato: $(cat /sys/class/udc/$UDC_NAME/state 2>/dev/null)  velocità: $(cat /sys/class/udc/$UDC_NAME/current_speed 2>/dev/null)"
    echo "   funzione attiva: $(linked | tr '\n' ' ')"
    for f in "$G"/functions/*.usb0; do
        [ -d "$f" ] || continue
        echo "   $(basename "$f"): interfaccia $(cat "$f/ifname" 2>/dev/null), MAC host $(cat "$f/host_addr" 2>/dev/null), MAC camera $(cat "$f/dev_addr" 2>/dev/null)"
    done
    echo "== Bridge $BR"
    ip -4 -o addr show "$BR" 2>/dev/null | awk '{print "   " $4}'
    echo "   interfacce: $(ls /sys/class/net/$BR/brif 2>/dev/null | tr '\n' ' ')"
    echo "== Indirizzi dati dal DHCP sul cavo USB"
    grep " 192\.168\.55\." /var/lib/misc/dnsmasq.leases 2>/dev/null | awk '{print "   " $3 "  MAC " $2 "  " $4}' \
        || echo "   (nessuno: l'host non ha ancora chiesto un indirizzo)"
}

case "${1:-stato}" in
    ecm|ncm) switch_to "$1" ;;
    rndis)   restore ;;
    stato)   show ;;
    *)       echo "uso: sh $0 ecm|ncm|rndis|stato"; exit 2 ;;
esac
