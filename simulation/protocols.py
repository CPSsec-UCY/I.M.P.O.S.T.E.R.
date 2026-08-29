"""Real industrial protocol services for the PV plant simulator.

These turn the *simulated* plant into something a real client/SCADA can talk
to. The simulation (``PVPlant``) is the single source of truth; each service
below exposes that same live state over a standard wire protocol:

  * Modbus TCP  (port 502)   - pymodbus, read/write, two-way (writes drive the plant)
  * IEC 60870-5-104 (port 2404) - minimal but real 104 server emitting ASDUs
  * IEC 61850 GOOSE (EtherType 0x88B8) - scapy-published GOOSE frames on events

All services are defensive: a failure in one (e.g. no permission to open raw
sockets for GOOSE) must not take the others or the web HMI down.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from datetime import datetime

try:
    import scapy.all as scapy
    HAVE_SCAPY = True
except Exception:  # pragma: no cover
    HAVE_SCAPY = False

from simulation.plants.equipment import (
    EQUIP_COIL_BASE, EQUIP_REG_BASE, REG_PER_EQ, eq_binary,
)


# =====================================================================
# 1) Modbus TCP  (small self-contained server, fully two-way)
# =====================================================================
class ModbusService:
    """A minimal but real Modbus TCP server.

    The store is refreshed from the plant each tick (push), and client
    writes to coils are mapped back onto plant controls, so a Modbus
    master can actually operate the plant (trip inverters, drop the grid,
    pause the simulation)."""

    def __init__(self, plant, port=502):
        self.plant = plant
        self.port = port
        self.enabled = True
        self.listening = False
        self.clients = 0
        try:
            n_inv = len(plant.inverters)
        except Exception:
            n_inv = 6
        self.n_inv = n_inv
        n_eq = len(getattr(plant, "equipment", []))
        self.reg_size = max(2000,
                            (EQUIP_REG_BASE + n_eq * REG_PER_EQ + 50) - 40001)
        self.coil_size = max(128, n_inv + 12,
                             EQUIP_COIL_BASE + n_eq + 8)
        self.coils = [False] * self.coil_size
        self.di = [True] * self.coil_size
        self.hr = [0] * self.reg_size
        self.ir = [0] * self.reg_size

    # ---- refresh store from plant ----
    def push(self):
        m = self.plant.modbus_map()
        hr = [0] * self.reg_size
        for addr, _name, raw, _unit in m["holding_registers"]:
            if 0 <= addr - 40001 < len(hr):
                hr[addr - 40001] = raw
        co = [False] * self.coil_size
        for addr, _name, val in m["coils"]:
            if 0 <= addr < len(co):
                co[addr] = bool(val)
        self.hr = hr
        self.ir = list(hr)
        self.di = list(co)
        # Coils mirror the plant's live status (readback). Operator writes are
        # applied immediately in the write handlers via _set_coil(); we must NOT
        # re-apply them here, otherwise the plant's own reported state (e.g.
        # "grid connected") would be fed back as a command and re-trip it.
        self.coils = co

    def rebind(self, plant):
        """Point this service at a newly selected plant (same socket)."""
        self.plant = plant
        try:
            n_inv = len(plant.inverters)
        except Exception:
            n_inv = 6
        self.n_inv = n_inv
        self.coil_size = max(128, n_inv + 12)
        self.coils = [False] * self.coil_size
        self.di = [True] * self.coil_size
        self.push()

    def _set_coil(self, addr, value):
        n = self.n_inv
        # Equipment control coils (breakers / VFDs / pumps / wellhead valves ...)
        if addr >= EQUIP_COIL_BASE:
            eqs = getattr(self.plant, "equipment", None)
            if eqs is not None:
                idx = addr - EQUIP_COIL_BASE
                if 0 <= idx < len(eqs):
                    try:
                        self.plant.control_equipment(eqs[idx]["id"], bool(value))
                    except Exception as exc:
                        print(f"[modbus] equip control error: {exc}")
            return
        if 0 <= addr < n:
            self.plant.toggle_inverter(addr, bool(value))
        elif addr == n:
            # coil True => grid connected (plant uses grid_ok / connected)
            self.plant.inject_grid_fault(not bool(value))
        elif addr == n + 1:
            self.plant.set_running(bool(value))
        # coils n+2 (curtailment) and n+3 (string fault) are status mirrors;
        # writes are ignored to avoid unintended trips.

    # ---- wire protocol ----
    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", self.port))
        except OSError as e:
            # Port 502 is privileged; fall back to 5020 when not root.
            if self.port == 502:
                try:
                    srv.bind(("0.0.0.0", 5020))
                    self.port = 5020
                except OSError as e2:
                    print(f"[modbus] bind failed: {e2}")
                    self.enabled = False
                    return
            else:
                print(f"[modbus] bind failed: {e}")
                self.enabled = False
                return
        srv.listen(5)
        self.listening = True
        print(f"[modbus] listening on tcp/{self.port}")
        threading.Thread(target=self._accept, args=(srv,), daemon=True).start()

    def _accept(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            self.clients += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _pack_bits(bits):
        out = bytearray()
        for i in range(0, len(bits), 8):
            byte = 0
            for j, b in enumerate(bits[i:i + 8]):
                if b:
                    byte |= (1 << j)
            out.append(byte)
        return bytes(out)

    @staticmethod
    def _unpack_bits(data, count):
        bits = []
        for byte in data:
            for j in range(8):
                bits.append(bool(byte & (1 << j)))
        return bits[:count]

    def _handle(self, conn):
        try:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 8:
                    # MBAP: trans(2) proto(2) len(2) unit(1) -> min 8 for header
                    length = struct.unpack(">H", buf[4:6])[0]
                    if len(buf) < 6 + length:
                        break
                    mbap = buf[:7]
                    pdu = buf[7:6 + length]
                    buf = buf[6 + length:]
                    resp = self._process(mbap, pdu)
                    if resp:
                        conn.sendall(resp)
        except Exception as e:
            print(f"[modbus] conn error: {e}")
        finally:
            self.clients = max(0, self.clients - 1)
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _mbap(trans, unit, pdu):
        """Build a proper Modbus TCP MBAP (trans, proto, length, unit) + PDU."""
        return trans + b"\x00\x00" + struct.pack(">H", len(pdu) + 1) + bytes([unit]) + pdu

    def _process(self, mbap, pdu):
        trans = mbap[0:2]
        unit = mbap[6]
        func = pdu[0]
        addr = struct.unpack(">H", pdu[1:3])[0]
        try:
            if func == 0x01:  # read coils
                qty = struct.unpack(">H", pdu[3:5])[0]
                vals = self.coils[addr:addr + qty]
                data = self._pack_bits(vals)
                return self._mbap(trans, unit, bytes([func, len(data)]) + data)
            if func == 0x02:  # read discrete inputs
                qty = struct.unpack(">H", pdu[3:5])[0]
                vals = self.di[addr:addr + qty]
                data = self._pack_bits(vals)
                return self._mbap(trans, unit, bytes([func, len(data)]) + data)
            if func in (0x03, 0x04):  # read holding / input registers
                qty = struct.unpack(">H", pdu[3:5])[0]
                src = self.hr if func == 0x03 else self.ir
                vals = src[addr:addr + qty]
                data = b"".join(struct.pack(">H", v) for v in vals)
                return self._mbap(trans, unit, bytes([func, len(data)]) + data)
            if func == 0x05:  # write single coil
                value = (pdu[3] == 0xFF)
                if 0 <= addr < len(self.coils):
                    self.coils[addr] = value
                    self._set_coil(addr, value)
                return self._mbap(trans, unit, bytes([func]) + pdu[1:5])
            if func == 0x06:  # write single register
                value = struct.unpack(">H", pdu[3:5])[0]
                if 0 <= addr < len(self.hr):
                    self.hr[addr] = value
                return self._mbap(trans, unit, bytes([func]) + pdu[1:5])
            if func == 0x0F:  # write multiple coils
                qty = struct.unpack(">H", pdu[3:5])[0]
                bc = pdu[5]
                bits = self._unpack_bits(pdu[6:6 + bc], qty)
                for i, b in enumerate(bits):
                    if 0 <= addr + i < len(self.coils):
                        self.coils[addr + i] = b
                        self._set_coil(addr + i, b)
                return self._mbap(trans, unit, bytes([func]) + pdu[1:5])
            if func == 0x10:  # write multiple registers
                qty = struct.unpack(">H", pdu[3:5])[0]
                bc = pdu[5]
                regs = [struct.unpack(">H", pdu[6 + 2 * i:8 + 2 * i])[0]
                        for i in range(qty)]
                for i, v in enumerate(regs):
                    if 0 <= addr + i < len(self.hr):
                        self.hr[addr + i] = v
                return self._mbap(trans, unit, bytes([func]) + pdu[1:5])
            # unsupported function -> exception
            return self._mbap(trans, unit, bytes([func | 0x80, 0x01]))
        except Exception:
            return self._mbap(trans, unit, bytes([func | 0x80, 0x02]))

    def status(self):
        return {
            "name": "Modbus TCP",
            "port": self.port,
            "enabled": self.enabled,
            "listening": self.listening,
            "clients": self.clients,
        }


# =====================================================================
# 2) IEC 60870-5-104  (minimal real server)
# =====================================================================
# APCI control-field patterns (U-format)
U_STARTDT_ACT = b"\x07\x00"
U_STARTDT_CON = b"\x0b\x00"
U_STOPDT_ACT = b"\x13\x00"
U_STOPDT_CON = b"\x23\x00"
U_TESTFR_ACT = b"\x43\x00"
U_TESTFR_CON = b"\x83\x00"

# ASDU type identifiers
M_SP_NA = 1     # single point
M_ME_NC = 13    # measured value, short floating point
C_SC_NA = 58    # single command (control -> operate equipment)
C_IC_NA = 100   # interrogation command
C_RD_NA = 102   # read command

COT_SPONTANEOUS = 11
COT_INTERROGATED = 20
COT_REQUEST = 5


class IEC104Service:
    """A small but genuinely wire-compatible IEC 60870-5-104 server.

    It accepts a connection, performs the STARTDT handshake, answers
    interrogation (C_IC_NA) with the full process image, and then streams
    spontaneous M_SP_NA / M_ME_NC ASDUs as the plant evolves. Sequence
    numbers are maintained per-connection.
    """

    def __init__(self, plant, port=2404):
        self.plant = plant
        self.port = port
        self.enabled = True
        self.listening = False
        self.clients = 0
        self._lock = threading.Lock()

    # ---- public control ----
    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", self.port))
        except OSError as e:
            print(f"[iec104] bind failed: {e}")
            self.enabled = False
            return
        srv.listen(5)
        self.listening = True
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()

    def _accept_loop(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            with self._lock:
                self.clients += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    # ---- frame helpers ----
    @staticmethod
    def _i_frame(send_seq, recv_seq, asdu):
        hdr = bytes([0x68, len(asdu) + 4,
                     send_seq & 0xFF, (send_seq >> 8) & 0xFF,
                     recv_seq & 0xFF, (recv_seq >> 8) & 0xFF])
        return hdr + asdu

    @staticmethod
    def _u_frame(body):
        return b"\x68\x04" + body

    def _asdu(self, type_id, cot, ioa_start, elements):
        """Build an ASDU. ``elements`` is a list of byte-strings (info objects).
        SQ=0: each element carries its own sequential IOA starting at ioa_start."""
        vsq = len(elements) & 0x7F
        head = bytes([type_id, vsq,
                      cot & 0xFF, (cot >> 8) & 0xFF,  # COT (lo, hi w/ originator)
                      0x01, 0x00])                    # common address = 1
        body = b""
        for idx, el in enumerate(elements):
            body += struct.pack("<I", ioa_start + idx)[:3] + el  # IOA + data
        return head + body

    # ---- per-connection ----
    def _handle(self, conn):
        send_seq = 0
        recv_seq = 0
        started = False
        try:
            conn.settimeout(5.0)
            buf = b""
            while True:
                # periodic spontaneous transmission (after STARTDT)
                if started:
                    try:
                        asdus = self._build_spontaneous()
                        if asdus is not None:
                            send_seq = self._send_asdus(conn, asdus, send_seq, recv_seq)
                    except Exception as ex:
                        import traceback
                        traceback.print_exc()
                        print(f"[iec104] spontaneous error: {ex}")
                    time.sleep(1.0)
                # read available bytes
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                # parse complete APDUs (start 0x68)
                while len(buf) >= 2 and buf[0] == 0x68:
                    length = buf[1]
                    if len(buf) < 2 + length:
                        break
                    apdu = buf[:2 + length]
                    buf = buf[2 + length:]
                    try:
                        send_seq, recv_seq, started = self._on_apdu(
                            conn, apdu, send_seq, recv_seq, started)
                    except Exception as ex:
                        import traceback
                        traceback.print_exc()
                        print(f"[iec104] _on_apdu error: {ex}")
                if not started:
                    # give the client a moment to STARTDT
                    time.sleep(0.2)
        except Exception as e:
            print(f"[iec104] conn error: {e}")
        finally:
            with self._lock:
                self.clients = max(0, self.clients - 1)
            try:
                conn.close()
            except Exception:
                pass

    def _on_apdu(self, conn, apdu, send_seq, recv_seq, started):
        ctrl = apdu[2:6]
        if ctrl[0] & 0x03 == 0x03 and ctrl[1] == 0x00:
            # U-frame
            if ctrl[:2] == U_STARTDT_ACT:
                conn.sendall(self._u_frame(U_STARTDT_CON))
                started = True
            elif ctrl[:2] == U_TESTFR_ACT:
                conn.sendall(self._u_frame(U_TESTFR_CON))
            elif ctrl[:2] == U_STOPDT_ACT:
                conn.sendall(self._u_frame(U_STOPDT_CON))
                started = False
            elif ctrl[:2] == U_STOPDT_CON:
                pass
            return send_seq, recv_seq, started
        if (ctrl[0] & 0x01) == 0:
            # I-frame (information)
            s_seq = ctrl[0] | (ctrl[1] << 8)
            r_seq = ctrl[2] | (ctrl[3] << 8)
            recv_seq = (s_seq + 1) & 0xFFFF
            asdu = apdu[6:]
            if len(asdu) >= 2:
                type_id = asdu[0]
                if type_id == C_IC_NA:
                    # interrogation -> reply with full image
                    asdus = self._build_interrogation()
                    send_seq = self._send_asdus(conn, asdus, send_seq, recv_seq)
                elif type_id == C_RD_NA:
                    pass
                elif type_id == C_SC_NA:
                    # Single command: operate an equipment item (breaker / VFD /
                    # pump / wellhead valve ...). Info object = IOA(3) + SCO(1).
                    if len(asdu) >= 10:
                        ioa = struct.unpack("<I", asdu[6:9] + b"\x00")[0]
                        sco = asdu[9]
                        value = bool(sco & 0x01)
                        eqs = getattr(self.plant, "equipment", [])
                        if 1000 <= ioa < 1000 + len(eqs):
                            try:
                                self.plant.control_equipment(
                                    eqs[ioa - 1000]["id"], value)
                            except Exception as exc:
                                print(f"[iec104] equip control error: {exc}")
                        # confirmation (activation confirmation, COT=7)
                        confirm = self._asdu(C_SC_NA, 7, ioa,
                                             [bytes([sco & 0x01])])
                        send_seq = self._send_asdus(conn, [confirm],
                                                    send_seq, recv_seq)
            return send_seq, recv_seq, started
        # S-frame -> just bump recv seq
        return send_seq, recv_seq, started

    # ---- build ASDUs from plant ----
    def _chunk_asdus(self, type_id, cot, ioa_start, elements, max_elems=30):
        """Split a possibly-large element list into several <=255-byte ASDUs.

        Each element is 8 bytes (3-byte IOA + 5-byte value), so 30 elements fit
        in one APDU (30*8 + 6 header = 246 < 255). Sequential IOAs continue
        across chunks (SQ=0)."""
        out = []
        for s in range(0, len(elements), max_elems):
            out.append(self._asdu(type_id, cot, ioa_start + s,
                                  elements[s:s + max_elems]))
        return out

    def _measured(self, value):
        return struct.pack("<f", float(value)) + bytes([0x00])  # value + quality

    def _single(self, boolean):
        return bytes([0x01 if boolean else 0x00])  # value+quality in bit0

    def _send_asdus(self, conn, asdus, send_seq, recv_seq):
        """Send a list of ASDUs as individual I-frames (max 255-byte APDU)."""
        for asdu in asdus:
            conn.sendall(self._i_frame(send_seq, recv_seq, asdu))
            send_seq = (send_seq + 1) & 0xFFFF
        return send_seq

    def _build_equipment(self, cot):
        """Equipment single-point (IOA 1000+) and measured (IOA 2000+) ASDUs."""
        p = self.plant
        eqs = getattr(p, "equipment", [])
        if not eqs:
            return []
        sp = self._chunk_asdus(M_SP_NA, cot, 1000,
                               [self._single(eq_binary(e)) for e in eqs])
        els = []
        for e in eqs:
            meas = e.get("meas", [])
            v = meas[0][1] if meas else 0.0
            els.append(self._measured(v))
        meas_asdu = self._chunk_asdus(M_ME_NC, cot, 2000, els)
        return sp + meas_asdu

    def _build_interrogation(self):
        p = self.plant
        # single points: inverters 1..6 running, grid ok, plant run (IOA 1..8)
        bits = [i["available"] for i in p.plant["inverters"]]
        bits += [p.grid["connected"], p.running]
        sp = self._chunk_asdus(M_SP_NA, COT_INTERROGATED, 1,
                               [self._single(b) for b in bits])

        # per-inverter measured values (mirrors real SCADA tags):
        # IOA 100+i*4 : Real-time Active Power (kW)
        # IOA 101+i*4 : Real-time Reactive Power (kVAr)
        # IOA 102+i*4 : Phase B Current (A)
        # IOA 103+i*4 : Phase B Voltage (V)
        els = []
        for inv in p.plant["inverters"]:
            els.append(self._measured(inv["p_ac_kw"]))
            els.append(self._measured(inv["q_kvar"]))
            els.append(self._measured(inv["i_ac"]))
            els.append(self._measured(inv["v_phase"]))
        inv_meas = self._chunk_asdus(M_ME_NC, COT_INTERROGATED, 100, els)

        # plant-level measured values (IOA 300+)
        els = [
            self._measured(p.plant["p_ac_mw"] * 1000.0),
            self._measured(p.plant["q_total_kvar"]),
            self._measured(p.env["poa"]),
            self._measured(p.env["ambient_temp"]),
            self._measured(p.env["cell_temp"]),
            self._measured(p.grid["frequency"]),
            self._measured(p.grid["voltage_kv"]),
            self._measured(p.grid["power_factor"]),
            self._measured(p.plant["daily_energy_mwh"] * 1000.0),
        ]
        plant_meas = self._asdu(M_ME_NC, COT_INTERROGATED, 300, els)

        # equipment (IOA 1000+ single points, 2000+ measured)
        eq_asdus = self._build_equipment(COT_INTERROGATED)

        # termination of interrogation (type 100, COT=10)
        term = self._asdu(C_IC_NA, 10, 0, [bytes([0x00])])
        return sp + inv_meas + [plant_meas] + eq_asdus + [term]

    def _build_spontaneous(self):
        p = self.plant
        eqs = getattr(p, "equipment", [])
        eq_status = tuple(e["status"] for e in eqs)
        # Hash the changing analog signals; send if changed.
        sig = (round(p.plant["p_ac_mw"], 3), round(p.env["poa"], 0),
                 round(p.grid["frequency"], 3), p.grid["connected"],
                 tuple(i["available"] for i in p.plant["inverters"]),
                 eq_status)
        if not hasattr(self, "_last_hash"):
            self._last_hash = None
        if sig == self._last_hash:
            return None
        self._last_hash = sig
        # per-inverter + plant-level measured values (same IOA layout)
        els = []
        for inv in p.plant["inverters"]:
            els.append(self._measured(inv["p_ac_kw"]))
            els.append(self._measured(inv["q_kvar"]))
            els.append(self._measured(inv["i_ac"]))
            els.append(self._measured(inv["v_phase"]))
        inv_meas = self._chunk_asdus(M_ME_NC, COT_SPONTANEOUS, 100, els)
        els = [
            self._measured(p.plant["p_ac_mw"] * 1000.0),
            self._measured(p.plant["q_total_kvar"]),
            self._measured(p.env["poa"]),
            self._measured(p.env["ambient_temp"]),
            self._measured(p.env["cell_temp"]),
            self._measured(p.grid["frequency"]),
            self._measured(p.grid["voltage_kv"]),
            self._measured(p.grid["power_factor"]),
            self._measured(p.plant["daily_energy_mwh"] * 1000.0),
        ]
        plant_meas = self._asdu(M_ME_NC, COT_SPONTANEOUS, 300, els)
        bits = [i["available"] for i in p.plant["inverters"]]
        bits += [p.grid["connected"], p.running]
        sp = self._chunk_asdus(M_SP_NA, COT_SPONTANEOUS, 1,
                               [self._single(b) for b in bits])
        eq_asdus = self._build_equipment(COT_SPONTANEOUS)
        return inv_meas + [plant_meas] + sp + eq_asdus

    def status(self):
        return {
            "name": "IEC 60870-5-104",
            "port": self.port,
            "enabled": self.enabled,
            "listening": self.listening,
            "clients": self.clients,
        }


# =====================================================================
# 3) IEC 61850 GOOSE  (scapy, EtherType 0x88B8)
# =====================================================================
GOOSE_MCAST_MAC = "01:0c:cd:01:00:01"
GOOSE_VLAN = 0


def _ber(tag, value):
    """Minimal BER TLV encoder (supports definite lengths >= 128 via long form)."""
    length = len(value)
    if length < 128:
        len_bytes = bytes([length])
    else:
        nbytes = max(1, (length.bit_length() + 7) // 8)
        len_bytes = bytes([0x80 | nbytes]) + length.to_bytes(nbytes, "big")
    return bytes([tag]) + len_bytes + value


def _goose_apdu(gocb, dataset, st_num, sq_num, sim, conf_rev, t_utc, status_bits):
    """Construct a GOOSE APDU (IEC 61850-8-1) as raw bytes."""
    parts = []
    parts.append(_ber(0x80, gocb.encode()))            # gocbRef
    parts.append(_ber(0x81, struct.pack(">I", 1000)))  # timeAllowedToLive
    parts.append(_ber(0x82, dataset.encode()))         # datSet
    parts.append(_ber(0x83, gocb.encode()))            # goID
    parts.append(_ber(0x84, struct.pack(">I", st_num)))# stNum
    parts.append(_ber(0x85, struct.pack(">I", sq_num)))# sqNum
    parts.append(_ber(0x86, bytes([1 if sim else 0]))) # simulation
    parts.append(_ber(0x87, struct.pack(">I", conf_rev)))  # confRev
    parts.append(_ber(0x88, bytes([0])))              # ndsCom
    parts.append(_ber(0x89, t_utc))                    # T (UTCTime, 8 bytes)
    # data: AllData SEQUENCE (tag 0xAA constructed)
    data_inner = b""
    for b in status_bits:
        data_inner += _ber(0x83, bytes([1 if b else 0]))  # boolean
    parts.append(_ber(0xAA, data_inner))               # data
    return bytes([0x61]) + _ber(0x61, b"".join(parts))[1:]


class GOOSEService:
    def __init__(self, plant, iface=None, gateway_port=5880):
        self.plant = plant
        self.iface = iface or self._default_iface()
        kind = (getattr(plant, "KIND", "GEN") or "GEN").upper()
        self.gocb = f"{kind}_GCB_001"
        self.dataset = f"{kind}/LLN0$GO$Status"
        self.enabled = HAVE_SCAPY
        self.gateway_port = gateway_port
        self.gateway_enabled = True
        self.gateway_listening = False
        self.gateway_clients = 0
        self.published = 0
        self.messages = []
        self.st_num = 1
        self.sq_num = 0
        self._last_hash = None
        self._gw_clients = set()
        self._gw_lock = threading.Lock()

    @staticmethod
    def _default_iface():
        try:
            import scapy.all as _s
            return str(_s.conf.iface)
        except Exception:
            return "eth0"

    def _status_bits(self):
        bits = []
        eqs = getattr(self.plant, "equipment", [])
        for e in eqs:
            bits.append(eq_binary(e))
        bits += [i["available"] for i in self.plant.plant["inverters"]]
        bits += [self.plant.grid["connected"], self.plant.running]
        return bits

    def publish(self, reason="periodic"):
        if not self.enabled:
            return
        bits = self._status_bits()
        h = tuple(bits)
        if h != self._last_hash:
            self.st_num += 1
            self.sq_num = 0
            self._last_hash = h
        else:
            self.sq_num += 1

        t_utc = datetime.utcnow()
        # UTCTime: seconds since 1970 in 8 bytes (microseconds in low 24 bits)
        epoch = int(t_utc.timestamp() * 1000)
        t_bytes = struct.pack(">Q", epoch)  # 8 bytes

        apdu = _goose_apdu(
            gocb=self.gocb, dataset=self.dataset,
            st_num=self.st_num, sq_num=self.sq_num, sim=False,
            conf_rev=1, t_utc=t_bytes, status_bits=bits,
        )
        msg = {
            "time": t_utc.strftime("%H:%M:%S.%f")[:-3],
            "gocb": self.gocb,
            "stNum": self.st_num,
            "sqNum": self.sq_num,
            "reason": reason,
            "bits": bits,
            "dst": GOOSE_MCAST_MAC,
            "etype": "0x88B8",
        }
        self.messages.insert(0, msg)
        if len(self.messages) > 50:
            self.messages = self.messages[:50]
        self.published += 1

        # Stream the real GOOSE APDU to any TCP gateway subscribers
        # (length-prefixed so a client can re-assemble frames).
        self._gw_push(apdu)

        # Emit real Ethernet frame (best-effort, requires root for raw socket)
        try:
            pkt = scapy.Ether(dst=GOOSE_MCAST_MAC, src=scapy.get_if_hwaddr(self.iface),
                              type=0x88B8) / scapy.Raw(load=apdu)
            scapy.sendp(pkt, iface=self.iface, verbose=0)
        except Exception as e:
            msg["note"] = f"frame build/send skipped: {e}"

    def _gw_push(self, apdu):
        if not self._gw_clients:
            return
        frame = struct.pack(">I", len(apdu)) + apdu
        with self._gw_lock:
            dead = []
            for c in self._gw_clients:
                try:
                    c.sendall(frame)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._gw_clients.discard(c)

    def _gw_accept(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            with self._gw_lock:
                self._gw_clients.add(conn)
                self.gateway_clients = len(self._gw_clients)
            threading.Thread(target=self._gw_handle, args=(conn,), daemon=True).start()

    def _gw_handle(self, conn):
        try:
            while True:
                # Connection is a push sink: publish() streams frames to it.
                # A blocking recv only returns when the client closes, so we
                # can detect disconnect and clean up.
                chunk = conn.recv(1024)
                if not chunk:
                    break
        except Exception:
            pass
        finally:
            with self._gw_lock:
                self._gw_clients.discard(conn)
                self.gateway_clients = len(self._gw_clients)
            try:
                conn.close()
            except Exception:
                pass

    def start_gateway(self):
        if not self.gateway_enabled:
            return
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", self.gateway_port))
        except OSError as e:
            print(f"[goose-gw] bind failed: {e}")
            return
        srv.listen(10)
        self.gateway_listening = True
        print(f"[goose-gw] listening on tcp/{self.gateway_port}")
        threading.Thread(target=self._gw_accept, args=(srv,), daemon=True).start()

    def start(self):
        if not self.enabled:
            return
        self.start_gateway()
        # initial + periodic publisher thread. Kept alive even if an individual
        # publish raises, so the TCP gateway keeps streaming to subscribers.
        def loop():
            import traceback
            try:
                self.publish("initial")
            except Exception:
                traceback.print_exc()
            while True:
                try:
                    self.publish("periodic")
                except Exception:
                    traceback.print_exc()
                time.sleep(2.0)
        threading.Thread(target=loop, daemon=True).start()

    def status(self):
        return {
            "name": "IEC 61850 GOOSE",
            "iface": str(self.iface),
            "enabled": self.enabled,
            "published": self.published,
            "multicast_mac": GOOSE_MCAST_MAC,
            "ethertype": "0x88B8",
            "gateway_port": self.gateway_port,
            "gateway_listening": self.gateway_listening,
            "gateway_clients": self.gateway_clients,
            "messages": self.messages[:10],
        }


# =====================================================================
# Hub
# =====================================================================
class ProtocolHub:
    def __init__(self, plant, modbus_port=502, iec104_port=2404, goose_iface=None,
                 goose_gateway_port=5880):
        self.plant = plant
        self.modbus = ModbusService(plant, modbus_port)
        self.iec104 = IEC104Service(plant, iec104_port)
        self.goose = GOOSEService(plant, goose_iface, goose_gateway_port)

    def start(self):
        self.modbus.start()
        self.iec104.start()
        self.goose.start()

    def rebind(self, plant):
        """Repoint all services at a newly selected plant (sockets stay bound)."""
        self.plant = plant
        self.modbus.rebind(plant)
        self.iec104.plant = plant
        self.goose.plant = plant

    def status(self):
        return {
            "modbus": self.modbus.status(),
            "iec104": self.iec104.status(),
            "goose": self.goose.status(),
        }
