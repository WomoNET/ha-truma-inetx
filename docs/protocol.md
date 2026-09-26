# iNet X Bluetooth protocol

Notes on the protocol as implemented by this integration. The protocol was
worked out by others: [daaaaan](https://github.com/daaaaan/truma-inetx-ble)
documented the framing and message types, and
[guwo65](https://github.com/guwo65) tested pairing, routing and the command
sequences step by step on a panel with a Truma Combi 6 heater.

Nothing here comes from Truma. Where the meaning of a value is inferred from
observation, the text says so.

## Bluetooth link

- The panel advertises as `Truma iNetX-XXXXXX` with manufacturer ID `0x0C73`.
  The suffix matches the last three bytes of the panel's public address.
- Unbonded clients are disconnected while GATT services are resolved, so
  pairing has to happen before service discovery. The panel accepts
  "Just Works" pairing while its pairing mode is active.
- Before bonding the panel uses changing random addresses. After bonding the
  host resolves them to the public identity address.
- The panel keeps a limited number of bonds. Pairing more phones can remove
  an older bond, after which that client is rejected until it pairs again.

## GATT characteristics

All in service `fc314000-f3b2-11e8-8eb2-f2801f1b9fd1`:

| Characteristic | Properties | Use |
|---|---|---|
| `fc314001` | write, notify | Transport handshake |
| `fc314002` | write without response | Outgoing frames |
| `fc314003` | notify | Incoming frames |
| `fc314004` | notify | Never subscribe: enabling it breaks the transport |

Notifications are enabled on `fc314001` first, then on `fc314003`. Before
disconnecting, they are disabled in reverse order. Dropping the link without
that leaves the panel ignoring its own buttons for a while.

## Transport handshake

Every frame is announced with its length and acknowledged after the transfer.
Both directions mirror each other on `fc314001`:

```
app -> panel                         panel -> app
app:   01 <length uint16 LE>         panel: 83 <length uint16 LE>
panel: 81 00 (ready)                 app:   03 00 (ready)
app:   frame on fc314002             panel: frame on fc314003
panel: F0 <status>                   app:   F0 01
```

An acknowledgement status of `01` means the transfer completed. The second
byte after `83` always equals the length of the frame that follows, which is
why it is read as an announcement. A frame larger than one write or
notification is split into several; the announced length tells where it ends.

## Frames

| Bytes | Field |
|---|---|
| 0-1 | Destination address (uint16 LE) |
| 2-3 | Source address (uint16 LE) |
| 4-5 | Size: 9 + payload length |
| 6 | Control type: `01` registration, `03` message broker |
| 7-15 | Segment header; byte 7 holds the segment flags, zero for normal frames |
| 16 | Message type |
| 17 | Correlation ID |
| 18- | CBOR body |

Addresses seen so far:

| Address | Node |
|---|---|
| `0x0000` | Message broker |
| `0x0101` | Panel, owns `RoomClimate` |
| `0x0201` | Heater, owns `AirHeating`, `WaterHeating`, `AirCirculation`, `EnergySrc` |
| `0x0500` | Application before registration |
| `0x05xx` | Application address assigned by the panel |
| `0xFFFF` | Broadcast |

The high byte of an address matches the `Identify/Type` value the node
reports (1 for the panel, 2 for the heater).

## Session

1. **Registration**: send `{"pv": [5, 1]}` with control type `01` and
   message type `01` from `0x0500` to `0xFFFF`. The panel answers with
   `{"pv": [5, 1], "addr": <address>}`; that address is the source of all
   later frames.
2. **Parameter discovery**: message type `04` with body `{"tn": <topic>}` to
   a node. The node answers with its complete schema, not only the requested
   topic, in several frames of type `84`, followed by `{"LastMessage": 1}`
   with the correlation ID of the request.
3. **Subscription**: message type `02` to the message broker with
   `{"tn": [<topics>]}`, at most ten topics per request. Without it, changes
   made at the panel are not reported.
4. **Updates**: message type `00`, broadcast, one parameter per frame.
5. **Writes**: message type `01` with `{"tn", "pn", "v", "id": 0}` to the
   node that owns the topic. A write sent to another node is acknowledged by
   the transport but has no effect.

## Parameter descriptions

Discovery responses and updates describe parameters with these keys:
`tn` topic, `pn` parameter, `v` value, `type`, `min`, `max`, `avail`
(1 when available), `perm` (0 on read-only parameters) and `enum` (a list of
`{"n": name, "v": value, "a": available}`).

Temperatures (type 10) are integers in tenths of a degree Celsius.

## Command sequences

These sequences were verified on a Combi 6 system:

| Action | Writes, in order |
|---|---|
| Heating on / off | `RoomClimate/Mode` = 3 / 0 (panel) |
| Target temperature | `AirHeating/TgtTemp` (heater) |
| Ventilation on | `RoomClimate/Mode` = 5, then `AirCirculation/Active` = 1 |
| Fan level | `AirCirculation/FanLevel` 0 to 10, only after ventilation is on |
| Ventilation off | `AirCirculation/Active` = 0 (the panel then reports `RoomClimate/Mode` = 0) |
| Hot water on | `WaterHeating/Active` = 1; the heater reports 2 ("ready"), later 1 |
| Hot water level | `WaterHeating/Mode` 0 / 1 / 2 (40 / 60 / 70 °C), after "ready" when it was off |

Heater writes are not always echoed as updates, so the integration shows a
written value until the heater reports otherwise.
