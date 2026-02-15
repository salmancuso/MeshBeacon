# Solar Propagation Broadcast — `solar_broadcast.py`

**HF/VHF propagation forecasts and space weather alerting for MeshCore mesh networks**

Fetches solar indices, HF band conditions, VHF propagation data, and NOAA space weather alerts, then broadcasts formatted messages to MeshCore channels.

---

## Data Sources

**HamQSL / N0NBH Solar Data**
- URL: `hamqsl.com/solarxml.php`
- Free, no API key required
- Update frequency: Every 3 hours
- Data: SFI, Sunspot Number, A/K indices, X-ray flux, solar wind, magnetic field, HF band conditions, VHF phenomena

**Open-Meteo Pressure Level Data**
- URL: `api.open-meteo.com/v1/forecast`
- Free, no API key required
- Update frequency: Hourly
- Data: Temperature and humidity at 850hPa/925hPa for tropospheric inversion index

---

## Usage

```bash
# Solar indices + HF bands (2 messages)
python solar_broadcast.py

# Add VHF conditions + tropo index (4 messages total)
python solar_broadcast.py --vhf

# Individual HF band conditions (1 message)
python solar_broadcast.py --hfband

# Specific channel and delay
python solar_broadcast.py --channel meshhams --delay 10

# Preview without transmitting
python solar_broadcast.py --dry-run
```

---

## Features

- **Solar Indices**: SFI, Sunspot Number, A/K indices, X-ray flux
- **HF Band Conditions**: 80m through 10m, day/night predictions
- **VHF/UHF Propagation**: E-skip, aurora, tropospheric ducting
- **Tropo Index**: Custom calculation for San Jose using atmospheric pressure levels
- **NOAA Alerts**: Automatic alerts for geomagnetic storms (G-scale), solar flares (R-scale), proton events (S-scale)
- **Meteor Showers**: Tracks proximity to major meteor shower peaks (affects VHF propagation)

---

## Message Formats

**Solar Indices**
```
☀️ SOLAR: SFI=185 SN=85 A=5 K=2 Xray=A1.2 Wind=425km/s Bt=-3nT [10 Feb 1800z] ✅
```

**HF Bands**
```
📡 BANDS D/N: 80/40=✅/✅ 30/20=✅/🟡 17/15=🟡/❌ 12/10=❌/❌
```

**VHF Conditions** (with `--vhf`)
```
🔭 VHF: Es=Band Closed Aurora=No Aurora Meteor=Perseids+14d ❌
```

**Tropospheric Ducting** (with `--vhf`)
```
🌊 TROPO SJC: Idx=6/10 dT=+12F@925mb Pres=1022mb ✅ Likely
```

**NOAA Space Weather Alert** (automatic when conditions warrant)
```
💥🔆💫 ALERT: Geomag=G2(K=6+) Flare=M5.1(R2) Wind=650km/s
```

---

## Status Icons

- ✅ Good conditions
- 🟡 Fair conditions
- ❌ Poor / closed

---

## Automated Scheduling

```bash
# Solar indices every 3 hours
0 7,10,13,16,19,22 * * * /usr/bin/python3 /home/sal/meshcore/solar_broadcast.py --channel meshhams >> ~/meshcore/logs/solar.log 2>&1

# Full report with VHF once daily
0 7 * * * /usr/bin/python3 /home/sal/meshcore/solar_broadcast.py --vhf --channel meshhams >> ~/meshcore/logs/solar.log 2>&1
```

HF conditions change slowly; every 3 hours aligns well with typical propagation shifts.

---

## Dependencies

```
requests>=2.31.0
meshcore>=0.1.0
```

No API keys required.
