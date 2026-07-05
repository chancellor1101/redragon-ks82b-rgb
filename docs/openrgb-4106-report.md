# OpenRGB issue #4106 — Redragon KS82-B (258a:0049) protocol writeup

Ready-to-paste comment for https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4106

---

## Redragon KS82-B — full RGB protocol + key map (working on Linux)

I reverse-engineered LED control for this board and have a working Linux tool
driving all keys, so here's the complete device report for whoever adds it.

**Device**
- USB `258a:0049`, iProduct "Gaming Keyboard" / iManufacturer "BY Tech", Sinowealth controller.
- **Full-size, 108 addressable per-key LEDs** (alpha block + F-row + nav cluster + numpad). Note the name "KS82" is misleading — this variant is full-size, *not* 82-key.

**Interfaces**
- IF0 (`proto 1`, boot keyboard) — normal typing.
- **IF1 — vendor control, Usage Page `0xFF00`, Usage `0x01` — LED control lives here.**

**LED control = a single HID Feature report (SET_REPORT), 382 bytes:**

| bytes | value | meaning |
|-------|-------|---------|
| 0     | `0x08` | report id |
| 1..3  | `0x0A 0x7A 0x01` | command header |
| 4..381 | 126 × RGB triples | per-slot color (108 map to real LEDs) |

Sent via `HIDIOCSFEATURE` on `/dev/hidrawN` (IF1). It **coexists with the kernel
HID driver** — no interface claim / driver detach needed, typing is unaffected.
A statically-set frame **persists** (no keep-alive needed).

**Safety note re: the disabled Sinowealth driver:** this is *only* the LED
feature-report path (report `0x08`). It does **not** touch the ISP/bootloader or
firmware read/write path that caused the bricking reports. A driver that limits
itself to sending report `0x08` LED frames on IF1 is safe on this board.

**IF1 HID report descriptor (verbatim):**
```
06 01 00 09 80 a1 01 85 01 19 81 29 83 15 00 25 01 95 03 75 01 81 02 95 01 75 05
81 01 c0 05 0c 09 01 a1 01 85 02 19 00 2a ff 02 15 00 26 ff 7f 95 01 75 10 81 00
c0 06 00 ff 09 01 a1 01 85 03 15 00 26 ff 00 09 2f 75 08 95 03 81 02 c0 05 01 09
06 a1 01 85 04 05 07 19 04 29 70 15 00 25 01 75 01 95 78 81 02 c0 06 00 ff 09 01
a1 01 85 05 15 00 26 ff 00 19 01 29 02 75 08 95 05 b1 02 c0 06 00 ff 09 01 a1 01
85 06 15 00 26 ff 00 19 01 29 02 75 08 96 07 04 b1 02 c0 05 01 09 02 a1 01 85 07
...
```
Relevant vendor feature reports: id `0x05` (5 bytes), id `0x06` (1796 bytes,
unused for basic LED set), **id `0x08` (381 data bytes = the per-key color
buffer)**.

**Slot → key map (slot index in the 126-triple array; `----` = no LED):**
```
  0:Esc   1:`   2:Tab   3:Caps   4:LShift   5:LCtrl
  6:----   7:1   8:Q   9:A  10:Z  11:Win
 12:F1  13:2  14:W  15:S  16:X  17:LAlt
 18:F2  19:3  20:E  21:D  22:C  23:----
 24:F3  25:4  26:R  27:F  28:V  29:----
 30:F4  31:5  32:T  33:G  34:B  35:Space
 36:F5  37:6  38:Y  39:H  40:N  41:----
 42:F6  43:7  44:U  45:J  46:M  47:----
 48:F7  49:8  50:I  51:K  52:,  53:RAlt
 54:F8  55:9  56:O  57:L  58:.  59:Fn
 60:F9  61:0  62:P  63:;  64:/  65:Menu
 66:F10  67:-  68:[  69:'  70:----  71:----
 72:F11  73:=  74:]  75:----  76:----  77:----
 78:F12  79:Bksp  80:\  81:Enter  82:RShift  83:RCtrl
 84:PrtSc  85:Ins  86:Del  87:----  88:----  89:Left
 90:ScrLk  91:Home  92:End  93:----  94:Up  95:Down
 96:Pause  97:PgUp  98:PgDn  99:---- 100:---- 101:Right
102:Mute 103:NumLock 104:Num7 105:Num4 106:Num1 107:Num0
108:Calc 109:NumDiv 110:Num8 111:Num5 112:Num2 113:----
114:ScreenLock 115:NumMult 116:Num9 117:Num6 118:Num3 119:NumDot
120:ShowDesktop 121:NumMinus 122:NumPlus 123:---- 124:NumEnter 125:----
```
(102/108/114/120 = the media strip above the numpad: Mute, Calculator, Screen
Lock, Show-Desktop.)

Derived by extending EvanSunde/Sinodragon (which targets `258a:0049` but with a
numpad-less 16×6 = 96-slot layout) to the full 126-slot buffer, then calibrating
each slot on hardware. Working standalone tool + this map is public if useful.
```
