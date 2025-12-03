# API Guide: Multi-Room Booking via `addBookingUserRest`

## Endpoint Details

| Property | Value |
|----------|-------|
| **URL** | `POST /api/addBookingUserRest` |
| **Content-Type** | `application/x-www-form-urlencoded` or `multipart/form-data` |
| **Authentication** | None required (local requests) |

---

## Request Fields

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `titulo` | string | Title (Sr., Sra., Sres.) | `"Sres."` |
| `firstname` | string | First name | `"RESERVADO"` |
| `lastname` | string | Last name | `"EVENTO"` |
| `phone` | string | Phone number | `"25052800"` |
| `reserverooms` | string | Room allocation (see format below) | `"24+25+26+27"` |
| `checkIn` | string | Check-in date (YYYY-MM-DD) | `"2026-02-14"` |
| `checkOut` | string | Check-out date (YYYY-MM-DD) | `"2026-02-15"` |
| `acomodacion` | string | Accommodation type | `"Bungalow Junior..."` |
| `adultcount` | string | Adult count(s) - supports `+` delimiter | `"2+3+2+4"` |
| `childcount` | string | Children <6 years | `"0"` |
| `childcount1` | string | Children 6-10 years | `"0"` |
| `payway` | string | Payment method | `"Cortesía"` |
| `reseramount` | string | Total reservation amount | `"85.86"` |
| `loadamount` | string | Amount to charge | `"58.86"` |
| `dui` | string | ID document | `"00000000-0"` |
| `national` | string | Nationality | `"Salvadoreño"` |
| `adultrate` | string | Rate per adult | `"0"` |
| `childrate` | string | Rate per child | `"0"` |
| `cardusername` | string | Card holder name | `"RESERVADO"` |
| `service` | string | Service type | `"Estadía"` |
| `username` | string | Booking agent username | `"RECEPCION"` |
| `certificado` | string | Certificate number | `"855"` |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `commenthotel` | string | Hotel comments |
| `ciudad` | string | City |
| `email` | string | Guest email |
| `cardnumber` | string | Card number |
| `duedate` | string | Card expiration date |
| `comment` | string | Additional comments |
| `compraclick` | string | Compraclick authorization |
| `codigo` | string | Certificate code |
| `discountAmount` | string | Discount amount |

---

## Room & PAX Formats

### `reserverooms` Field Format

| Scenario | Format | Example |
|----------|--------|---------|
| **Single room, single night** | `"ROOM"` | `"24"` |
| **Simultaneous rooms, single night** | `"ROOM1+ROOM2+ROOM3"` | `"24+25+26+27"` |
| **Sequential rooms, multi-night** | `"1-ROOM1,ROOM2,ROOM3"` | `"1-24,25,26"` |
| **Mixed (simultaneous + sequential)** | `"1-ROOM1+ROOM2,ROOM3+ROOM4"` | `"1-24+25,26+27"` |

### `adultcount` Field Format

| Scenario | Format | Example | Meaning |
|----------|--------|---------|---------|
| **Single room** | `"N"` | `"8"` | 8 adults total |
| **Simultaneous rooms (same PAX)** | `"N"` | `"2"` | 2 adults per room (backward compatible) |
| **Simultaneous rooms (different PAX)** | `"N1+N2+N3+N4"` | `"2+3+2+4"` | Room 1: 2, Room 2: 3, Room 3: 2, Room 4: 4 |

**Important:** The order of PAX values in `adultcount` MUST match the order of rooms in `reserverooms`.

---

## Example Requests

### Example 1: Single Room Booking

```bash
curl -X POST http://localhost/api/addBookingUserRest \
  -d "titulo=Sr." \
  -d "firstname=JUAN" \
  -d "lastname=PEREZ" \
  -d "phone=70001234" \
  -d "reserverooms=24" \
  -d "checkIn=2026-02-14" \
  -d "checkOut=2026-02-15" \
  -d "acomodacion=Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas." \
  -d "adultcount=2" \
  -d "childcount=0" \
  -d "childcount1=0" \
  -d "payway=Tarjeta de crédito" \
  -d "reseramount=138.00" \
  -d "loadamount=138.00" \
  -d "dui=12345678-9" \
  -d "national=Salvadoreño" \
  -d "adultrate=69" \
  -d "childrate=34.5" \
  -d "cardusername=JUAN PEREZ" \
  -d "service=Paquete Las Hojas" \
  -d "username=Reservas1" \
  -d "certificado=855"
```

### Example 2: Simultaneous Multi-Room Booking (Same PAX per room)

```bash
curl -X POST http://localhost/api/addBookingUserRest \
  -d "titulo=Sres." \
  -d "firstname=RESERVADO" \
  -d "lastname=EVENTO" \
  -d "phone=25052800" \
  -d "reserverooms=24+25+26+27" \
  -d "checkIn=2026-02-14" \
  -d "checkOut=2026-02-15" \
  -d "acomodacion=Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas." \
  -d "adultcount=2" \
  -d "childcount=0" \
  -d "childcount1=0" \
  -d "payway=Cortesía" \
  -d "reseramount=343.44" \
  -d "loadamount=235.44" \
  -d "dui=00000000-0" \
  -d "national=Salvadoreño" \
  -d "adultrate=0" \
  -d "childrate=0" \
  -d "cardusername=RESERVADO" \
  -d "service=Estadía" \
  -d "username=RECEPCION" \
  -d "certificado=855"
```

**Result:** All 4 rooms show PAX = 2

### Example 3: Simultaneous Multi-Room Booking (Different PAX per room)

```bash
curl -X POST http://localhost/api/addBookingUserRest \
  -d "titulo=Sres." \
  -d "firstname=GRUPO" \
  -d "lastname=CORPORATIVO" \
  -d "phone=25052800" \
  -d "reserverooms=24+25+26+27" \
  -d "checkIn=2026-02-14" \
  -d "checkOut=2026-02-15" \
  -d "acomodacion=Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas." \
  -d "adultcount=2+3+2+4" \
  -d "childcount=0" \
  -d "childcount1=0" \
  -d "payway=Depósito a cuenta BAC" \
  -d "reseramount=500.00" \
  -d "loadamount=500.00" \
  -d "dui=00000000-0" \
  -d "national=Salvadoreño" \
  -d "adultrate=45" \
  -d "childrate=22.5" \
  -d "cardusername=EMPRESA SA DE CV" \
  -d "service=Evento" \
  -d "username=RECEPCION" \
  -d "certificado=855"
```

**Result:**
- Room 24: PAX = 2
- Room 25: PAX = 3
- Room 26: PAX = 2
- Room 27: PAX = 4

---

## JSON Request Body Alternative

If you prefer JSON format:

```json
{
  "titulo": "Sres.",
  "firstname": "GRUPO",
  "lastname": "CORPORATIVO",
  "phone": "25052800",
  "reserverooms": "24+25+26+27",
  "checkIn": "2026-02-14",
  "checkOut": "2026-02-15",
  "acomodacion": "Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas.",
  "adultcount": "2+3+2+4",
  "childcount": "0",
  "childcount1": "0",
  "payway": "Cortesía",
  "reseramount": "500.00",
  "loadamount": "500.00",
  "dui": "00000000-0",
  "national": "Salvadoreño",
  "adultrate": "45",
  "childrate": "22.5",
  "cardusername": "EMPRESA SA DE CV",
  "service": "Evento",
  "username": "RECEPCION",
  "certificado": "855"
}
```

---

## Validation Rules

1. **Room-PAX Alignment:** If using `+` in `adultcount`, the number of values MUST equal the number of rooms in `reserverooms`
   - ✅ `reserverooms=24+25+26+27` with `adultcount=2+3+2+4` (4 rooms, 4 values)
   - ❌ `reserverooms=24+25+26+27` with `adultcount=2+3` (4 rooms, 2 values)

2. **Backward Compatibility:** A single value in `adultcount` applies to ALL rooms
   - `reserverooms=24+25+26+27` with `adultcount=2` → All rooms show PAX = 2

3. **Date Format:** Use `YYYY-MM-DD` format for `checkIn` and `checkOut`

4. **Valid Room Numbers:**
   ```
   1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20,
   21, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41,
   43, 44, 45, 46, 49, 50, 51, 52, 54, 55, 56, 57, 58, 59,
   1A, 2A, 3A, 4A, 5A, 6A, 7A, 8A, 9A, 10A, 11A, 12A, 13A, 14A
   ```

5. **Service Types:** Valid values include:
   - `Estadía`
   - `Paquete Las Hojas`
   - `Paquete Escapadita`
   - `Pasadía`
   - `Evento`
   - `Cupón Club`
   - `Canje`
   - `Tour operador`
   - `Certificado de sala`

6. **Payment Methods (`payway`):**
   - `Tarjeta de crédito`
   - `Depósito a cuenta BAC`
   - `Cortesía`
   - `Efectivo`
   - `Transferencia`

7. **Accommodation Types (`acomodacion`):**
   - `Bungalow Familiar: 2 ambientes, 4 camas, 2 baños, terraza para hamacas.`
   - `Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas.`
   - `Bungalow Matrimonial: 1 ambiente, 1 cama, 1 baño, terraza para hamacas.`
   - `Habitación: 1 ambiente, 2 camas, 1 baño.`
   - `Pasadía`

---

## Response

### Success Response
```json
{
  "status": "success"
}
```

### Error Response
```json
{
  "message": "Error description here"
}
```

---

## Quick Reference: Multi-Room Booking

To book **4 rooms simultaneously** with **different PAX per room**:

```
reserverooms = "24+25+26+27"
adultcount   = "2+3+2+4"
```

This creates ONE booking record where:
- Room 24 displays PAX: 2
- Room 25 displays PAX: 3
- Room 26 displays PAX: 2
- Room 27 displays PAX: 4

The `+` delimiter links rooms that are booked together in the same reservation.
