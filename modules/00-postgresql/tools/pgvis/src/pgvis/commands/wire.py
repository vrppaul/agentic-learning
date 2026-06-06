import os
import socket
import struct
import tempfile

import click
import psycopg
from rich.text import Text

from pgvis.core import console
from pgvis.format import PanelBuilder, section_bar

MESSAGE_TYPES = {
    "Query": ("Simple query — SQL text sent as-is", "cyan"),
    "Parse": ("Extended protocol — prepare a statement with placeholders", "yellow"),
    "Bind": ("Extended protocol — bind parameter values to a prepared statement", "yellow"),
    "Describe": ("Extended protocol — request column descriptions", "yellow"),
    "Execute": ("Extended protocol — run the bound statement", "yellow"),
    "Sync": ("Extended protocol — process all pending messages", "yellow"),
    "Close": ("Close a prepared statement or portal", "dim"),
    "Flush": ("Request server to flush output buffer", "dim"),
    "RowDescription": ("Column metadata: names, types, sizes", "green"),
    "DataRow": ("One row of result data", "green"),
    "CommandComplete": ("Query finished — shows operation and row count", "magenta"),
    "ReadyForQuery": ("Server is ready for the next query", "magenta"),
    "ParseComplete": ("Statement successfully prepared", "dim"),
    "BindComplete": ("Parameters successfully bound", "dim"),
    "CloseComplete": ("Statement/portal closed", "dim"),
    "NoData": ("Statement returns no data", "dim"),
    "ParameterDescription": ("Parameter types for a prepared statement", "dim"),
    "ErrorResponse": ("Error from server", "red"),
    "NoticeResponse": ("Warning/info from server", "yellow"),
    "AuthenticationOk": ("Authentication successful", "green"),
    "AuthenticationSASL": ("SCRAM-SHA-256 authentication requested", "yellow"),
    "AuthenticationSASLContinue": ("SCRAM-SHA-256 challenge", "yellow"),
    "AuthenticationSASLFinal": ("SCRAM-SHA-256 verification", "yellow"),
    "ParameterStatus": ("Server parameter sent to client", "dim"),
    "BackendKeyData": ("Process ID and secret key for cancel requests", "dim"),
    "NegotiateProtocolVersion": ("Protocol version negotiation", "dim"),
}

BACKEND_MSG_TYPES = {
    ord("R"): "Authentication",
    ord("K"): "BackendKeyData",
    ord("S"): "ParameterStatus",
    ord("Z"): "ReadyForQuery",
    ord("T"): "RowDescription",
    ord("D"): "DataRow",
    ord("C"): "CommandComplete",
    ord("E"): "ErrorResponse",
    ord("N"): "NoticeResponse",
    ord("1"): "ParseComplete",
    ord("2"): "BindComplete",
    ord("3"): "CloseComplete",
    ord("n"): "NoData",
    ord("t"): "ParameterDescription",
    ord("I"): "EmptyQueryResponse",
}


@click.command()
@click.argument("sql_text")
@click.option("--extended", is_flag=True, help="Use extended query protocol (Parse/Bind/Execute).")
@click.pass_context
def wire(ctx, sql_text, extended):
    """Show PostgreSQL wire protocol messages with decoded bytes.

    Example: pgvis wire "SELECT * FROM accounts WHERE id = 1"
             pgvis wire --extended "SELECT * FROM accounts WHERE id = 1"
    """
    dsn = ctx.obj["dsn"]
    parts = _parse_dsn(dsn)
    host = parts.get("host", "localhost")
    port = int(parts.get("port", "5432"))
    user = parts.get("user", "study")
    database = parts.get("database", "study")
    password = parts.get("password", "")

    p = PanelBuilder()
    p.add(section_bar(0x0, "WIRE PROTOCOL", "raw bytes + decoded", "cyan"))
    p.blank()

    stmt = Text()
    stmt.append(f"  {'':>6}    ", style="dim")
    stmt.append(f"  {sql_text}", style="white bold")
    p.add(stmt)
    p.blank()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))

    startup = _build_startup(user, database)
    _show_bytes(p, "CLIENT →", "StartupMessage", startup, [
        _field(0, 4, "int32", "length", len(startup)),
        _field(4, 4, "int32", "protocol", "3.0 (196608)"),
        _field(8, len(f"user\x00"), "string", "key", "user"),
        _field(8 + len(f"user\x00"), len(f"{user}\x00"), "string", "value", user),
        _field(8 + len(f"user\x00") + len(f"{user}\x00"), len(f"database\x00"), "string", "key", "database"),
        _field(8 + len(f"user\x00") + len(f"{user}\x00") + len(f"database\x00"), len(f"{database}\x00"), "string", "value", database),
    ], "cyan", "No type byte — the only message without one. Client identifies itself.")
    sock.sendall(startup)

    auth_type, auth_msgs = _handle_auth(sock, p, password, user)

    param_count = 0
    while True:
        msg_type, payload = _read_message(sock)
        name = BACKEND_MSG_TYPES.get(msg_type, f"Unknown({msg_type})")

        if msg_type == ord("S"):
            key, val = _parse_parameter_status(payload)
            param_count += 1
            if param_count <= 3:
                _show_bytes(p, "← SERVER", f"ParameterStatus", bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload, [
                    _field(0, 1, "byte", "type", f"'S' (0x{msg_type:02X})"),
                    _field(1, 4, "int32", "length", len(payload) + 4),
                    _field(5, len(key) + 1, "string", "param", key),
                    _field(5 + len(key) + 1, len(val) + 1, "string", "value", val),
                ], "dim", "Server tells client about a configuration parameter.")
            elif param_count == 4:
                skip = Text()
                skip.append(f"  {'':>6}    ", style="dim")
                skip.append(f"  ... more ParameterStatus messages (server_encoding, timezone, etc.)", style="dim italic")
                p.add(skip)
                p.blank()

        elif msg_type == ord("K"):
            pid = struct.unpack("!i", payload[:4])[0]
            key = struct.unpack("!i", payload[4:8])[0]
            _show_bytes(p, "← SERVER", "BackendKeyData", bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload, [
                _field(0, 1, "byte", "type", f"'K' (0x{msg_type:02X})"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 4, "int32", "pid", pid),
                _field(9, 4, "int32", "secret", key),
            ], "dim", "Backend PID and cancel key. Client stores these to cancel queries later.")

        elif msg_type == ord("Z"):
            status = chr(payload[0])
            status_desc = {"I": "Idle", "T": "In transaction", "E": "Failed transaction"}.get(status, status)
            _show_bytes(p, "← SERVER", "ReadyForQuery", bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload, [
                _field(0, 1, "byte", "type", f"'Z' (0x{msg_type:02X})"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 1, "byte", "status", f"'{status}' = {status_desc}"),
            ], "magenta", "Connection is ready. Now we send the query.")
            break

    if extended:
        _send_extended_query(sock, p, sql_text)
    else:
        query_msg = _build_query(sql_text)
        _show_bytes(p, "CLIENT →", "Query", query_msg, [
            _field(0, 1, "byte", "type", "'Q' (0x51)"),
            _field(1, 4, "int32", "length", len(query_msg) - 1),
            _field(5, len(sql_text) + 1, "string", "query", sql_text),
        ], "cyan", "Simple query protocol — send SQL as text, get results back.")
        sock.sendall(query_msg)

    while True:
        msg_type, payload = _read_message(sock)
        name = BACKEND_MSG_TYPES.get(msg_type, f"Unknown({msg_type})")
        raw = bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload

        if msg_type == ord("T"):
            ncols = struct.unpack("!h", payload[:2])[0]
            cols = _parse_row_description(payload)
            fields = [
                _field(0, 1, "byte", "type", "'T' (0x54)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 2, "int16", "num_cols", ncols),
            ]
            for col in cols:
                fields.append(_field(0, 0, "desc", f"col '{col['name']}'", f"type_oid={col['type_oid']} size={col['size']}"))
            _show_bytes(p, "← SERVER", "RowDescription", raw, fields, "green",
                        f"Describes {ncols} column(s) in the result set.")

        elif msg_type == ord("D"):
            ncols = struct.unpack("!h", payload[:2])[0]
            values = _parse_data_row(payload)
            fields = [
                _field(0, 1, "byte", "type", "'D' (0x44)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 2, "int16", "num_cols", ncols),
            ]
            for i, val in enumerate(values):
                fields.append(_field(0, 0, "desc", f"col {i+1}", repr(val)))
            _show_bytes(p, "← SERVER", "DataRow", raw, fields, "green",
                        "One row of data. Each column value is length-prefixed text.")

        elif msg_type == ord("C"):
            tag = payload[:-1].decode("utf-8")
            _show_bytes(p, "← SERVER", "CommandComplete", raw, [
                _field(0, 1, "byte", "type", "'C' (0x43)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, len(tag) + 1, "string", "tag", tag),
            ], "magenta", f"Query complete. '{tag}' = operation + row count.")

        elif msg_type == ord("Z"):
            status = chr(payload[0])
            status_desc = {"I": "Idle", "T": "In transaction", "E": "Failed transaction"}.get(status, status)
            _show_bytes(p, "← SERVER", "ReadyForQuery", raw, [
                _field(0, 1, "byte", "type", "'Z' (0x{:02X})".format(msg_type)),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 1, "byte", "status", f"'{status}' = {status_desc}"),
            ], "magenta", "Server is ready for the next query.")
            break

        elif msg_type == ord("1"):
            _show_bytes(p, "← SERVER", "ParseComplete", raw, [
                _field(0, 1, "byte", "type", "'1' (0x31)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
            ], "dim", "Statement parsed and planned successfully.")

        elif msg_type == ord("2"):
            _show_bytes(p, "← SERVER", "BindComplete", raw, [
                _field(0, 1, "byte", "type", "'2' (0x32)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
            ], "dim", "Parameters bound to statement. Portal ready to execute.")

        elif msg_type == ord("n"):
            _show_bytes(p, "← SERVER", "NoData", raw, [
                _field(0, 1, "byte", "type", "'n' (0x6E)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
            ], "dim", "Statement returns no data (e.g., INSERT/UPDATE/DELETE).")

        elif msg_type == ord("E"):
            err = _parse_error(payload)
            _show_bytes(p, "← SERVER", "ErrorResponse", raw, [
                _field(0, 1, "byte", "type", "'E' (0x45)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 0, "desc", "error", err),
            ], "red", "Server returned an error.")
            break

    sock.close()
    p.print(title="Wire Protocol — Raw", border_style="cyan")


def _field(offset, size, fmt, name, value):
    return {"offset": offset, "size": size, "fmt": fmt, "name": name, "value": value}


def _show_bytes(p, direction, msg_name, raw_bytes, fields, style, explanation=""):
    dir_style = "cyan" if "CLIENT" in direction else "green"

    header = Text()
    header.append(f"  {'':>6}    ", style="dim")
    header.append(f"  {direction}", style=dir_style)
    header.append(f"  {msg_name}", style=f"bold {style}")
    header.append(f"  ({len(raw_bytes)} bytes)", style="dim")
    p.add(header)

    if explanation:
        expl = Text()
        expl.append(f"  {'':>6}    ", style="dim")
        expl.append(f"  {'':>9}  ", style="dim")
        expl.append(explanation, style="dim italic")
        p.add(expl)

    hex_line = Text()
    hex_line.append(f"  {'':>6}    ", style="dim")
    hex_line.append(f"  {'':>9}  ", style="dim")
    hex_str = " ".join(f"{b:02X}" for b in raw_bytes[:40])
    if len(raw_bytes) > 40:
        hex_str += " ..."
    hex_line.append(hex_str, style="dim")
    p.add(hex_line)

    for f in fields:
        line = Text()
        line.append(f"  {'':>6}    ", style="dim")
        line.append(f"  {'':>9}  ", style="dim")
        line.append(f"  {f['name']:>12}", style=style)
        line.append(" = ", style="dim")
        line.append(str(f["value"]), style="white")
        p.add(line)

    p.blank()


def _build_startup(user, database):
    params = f"user\x00{user}\x00database\x00{database}\x00\x00".encode()
    length = 4 + 4 + len(params)
    return struct.pack("!ii", length, 196608) + params


def _build_query(sql):
    sql_bytes = sql.encode() + b"\x00"
    length = 4 + len(sql_bytes)
    return b"Q" + struct.pack("!i", length) + sql_bytes


def _read_message(sock):
    type_byte = sock.recv(1)
    if not type_byte:
        raise ConnectionError("Server closed connection")
    length_bytes = sock.recv(4)
    length = struct.unpack("!i", length_bytes)[0]
    payload = b""
    remaining = length - 4
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("Server closed connection")
        payload += chunk
        remaining -= len(chunk)
    return type_byte[0], payload


def _handle_auth(sock, p, password, user):
    msgs = []
    while True:
        msg_type, payload = _read_message(sock)
        if msg_type != ord("R"):
            return None, msgs

        auth_type = struct.unpack("!i", payload[:4])[0]
        raw = bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload

        if auth_type == 0:
            _show_bytes(p, "← SERVER", "AuthenticationOk", raw, [
                _field(0, 1, "byte", "type", "'R' (0x52)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 4, "int32", "auth_type", "0 = Ok"),
            ], "green", "Authentication successful. No password needed (trust mode).")
            return 0, msgs

        elif auth_type == 10:
            mechanisms = payload[4:].decode().split("\x00")
            mechanisms = [m for m in mechanisms if m]
            _show_bytes(p, "← SERVER", "AuthenticationSASL", raw, [
                _field(0, 1, "byte", "type", "'R' (0x52)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 4, "int32", "auth_type", "10 = SASL"),
                _field(9, 0, "desc", "mechanisms", ", ".join(mechanisms)),
            ], "yellow", "Server requests SCRAM-SHA-256 authentication.")

            _do_scram_auth(sock, p, password, user)
            return 10, msgs

        elif auth_type == 5:
            salt = payload[4:8]
            _show_bytes(p, "← SERVER", "AuthenticationMD5", raw, [
                _field(0, 1, "byte", "type", "'R' (0x52)"),
                _field(1, 4, "int32", "length", len(payload) + 4),
                _field(5, 4, "int32", "auth_type", "5 = MD5"),
                _field(9, 4, "bytes", "salt", salt.hex()),
            ], "yellow", "Server requests MD5 password authentication.")

            import hashlib
            pw_hash = hashlib.md5(password.encode() + user.encode()).hexdigest()
            full_hash = "md5" + hashlib.md5(pw_hash.encode() + salt).hexdigest()
            pw_msg = b"p" + struct.pack("!i", 4 + len(full_hash) + 1) + full_hash.encode() + b"\x00"
            sock.sendall(pw_msg)

            _show_bytes(p, "CLIENT →", "PasswordMessage", pw_msg, [
                _field(0, 1, "byte", "type", "'p' (0x70)"),
                _field(1, 4, "int32", "length", len(pw_msg) - 1),
                _field(5, len(full_hash) + 1, "string", "password", "md5{hash} (salted MD5)"),
            ], "cyan", "Client sends MD5(MD5(password+user)+salt).")

        else:
            _show_bytes(p, "← SERVER", f"Authentication({auth_type})", raw, [
                _field(0, 1, "byte", "type", "'R' (0x52)"),
                _field(5, 4, "int32", "auth_type", str(auth_type)),
            ], "yellow", f"Unsupported auth type {auth_type}.")
            break

    return None, msgs


def _do_scram_auth(sock, p, password, user):
    import hashlib
    import hmac
    import base64

    client_nonce = base64.b64encode(os.urandom(18)).decode()
    client_first = f"n,,n={user},r={client_nonce}"
    mechanism = b"SCRAM-SHA-256\x00"
    client_first_bare = f"n={user},r={client_nonce}"
    msg_data = mechanism + struct.pack("!i", len(client_first)) + client_first.encode()
    sasl_msg = b"p" + struct.pack("!i", 4 + len(msg_data)) + msg_data

    _show_bytes(p, "CLIENT →", "SASLInitialResponse", sasl_msg, [
        _field(0, 1, "byte", "type", "'p' (0x70)"),
        _field(1, 4, "int32", "length", len(sasl_msg) - 1),
        _field(5, len(mechanism), "string", "mechanism", "SCRAM-SHA-256"),
        _field(5 + len(mechanism), 4, "int32", "data_len", len(client_first)),
        _field(0, 0, "desc", "client_first", client_first_bare),
    ], "cyan", "Client sends nonce and username. The SCRAM handshake begins.")
    sock.sendall(sasl_msg)

    msg_type, payload = _read_message(sock)
    server_first = payload[4:].decode()
    raw = bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload

    server_parts = dict(kv.split("=", 1) for kv in server_first.split(","))
    server_nonce = server_parts["r"]
    salt = base64.b64decode(server_parts["s"])
    iterations = int(server_parts["i"])

    _show_bytes(p, "← SERVER", "AuthenticationSASLContinue", raw, [
        _field(0, 1, "byte", "type", "'R' (0x52)"),
        _field(5, 4, "int32", "auth_type", "11 = SASL Continue"),
        _field(0, 0, "desc", "server_nonce", server_nonce[:30] + "..."),
        _field(0, 0, "desc", "salt", base64.b64encode(salt).decode()),
        _field(0, 0, "desc", "iterations", str(iterations)),
    ], "yellow", f"Server sends salt + iteration count. Client must derive the key with PBKDF2 ({iterations} rounds).")

    salted_password = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted_password, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    client_final_no_proof = f"c=biws,r={server_nonce}"
    auth_message = f"{client_first_bare},{server_first},{client_final_no_proof}"
    client_signature = hmac.new(stored_key, auth_message.encode(), "sha256").digest()
    client_proof = bytes(a ^ b for a, b in zip(client_key, client_signature))
    client_final = f"{client_final_no_proof},p={base64.b64encode(client_proof).decode()}"

    sasl_response = b"p" + struct.pack("!i", 4 + len(client_final)) + client_final.encode()
    _show_bytes(p, "CLIENT →", "SASLResponse", sasl_response, [
        _field(0, 1, "byte", "type", "'p' (0x70)"),
        _field(1, 4, "int32", "length", len(sasl_response) - 1),
        _field(0, 0, "desc", "channel", "c=biws (no channel binding)"),
        _field(0, 0, "desc", "nonce", server_nonce[:30] + "..."),
        _field(0, 0, "desc", "proof", "HMAC(StoredKey, AuthMessage) XOR ClientKey"),
    ], "cyan", "Client proves it knows the password without sending it. Zero-knowledge proof.")
    sock.sendall(sasl_response)

    msg_type, payload = _read_message(sock)
    raw = bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload
    server_final = payload[4:].decode()
    _show_bytes(p, "← SERVER", "AuthenticationSASLFinal", raw, [
        _field(0, 1, "byte", "type", "'R' (0x52)"),
        _field(5, 4, "int32", "auth_type", "12 = SASL Final"),
        _field(0, 0, "desc", "server_sig", server_final[:40] + "..."),
    ], "yellow", "Server proves IT knows the password too. Mutual authentication.")

    msg_type, payload = _read_message(sock)
    raw = bytes([msg_type]) + struct.pack("!i", len(payload) + 4) + payload
    _show_bytes(p, "← SERVER", "AuthenticationOk", raw, [
        _field(0, 1, "byte", "type", "'R' (0x52)"),
        _field(5, 4, "int32", "auth_type", "0 = Ok"),
    ], "green", "SCRAM-SHA-256 complete. Both sides verified.")


def _send_extended_query(sock, p, sql_text):
    sql_bytes = sql_text.encode() + b"\x00"
    stmt_name = b"\x00"

    parse_payload = stmt_name + sql_bytes + struct.pack("!h", 0)
    parse_msg = b"P" + struct.pack("!i", 4 + len(parse_payload)) + parse_payload
    _show_bytes(p, "CLIENT →", "Parse", parse_msg, [
        _field(0, 1, "byte", "type", "'P' (0x50)"),
        _field(1, 4, "int32", "length", len(parse_msg) - 1),
        _field(5, 1, "string", "stmt_name", "(unnamed)"),
        _field(6, len(sql_bytes), "string", "query", sql_text),
        _field(6 + len(sql_bytes), 2, "int16", "num_params", 0),
    ], "yellow", "Prepare the statement. Server parses and plans it. Can be reused with different parameters.")

    portal_name = b"\x00"
    bind_payload = portal_name + stmt_name + struct.pack("!h", 0) + struct.pack("!h", 0) + struct.pack("!h", 0)
    bind_msg = b"B" + struct.pack("!i", 4 + len(bind_payload)) + bind_payload
    _show_bytes(p, "CLIENT →", "Bind", bind_msg, [
        _field(0, 1, "byte", "type", "'B' (0x42)"),
        _field(1, 4, "int32", "length", len(bind_msg) - 1),
        _field(5, 1, "string", "portal", "(unnamed)"),
        _field(6, 1, "string", "stmt", "(unnamed)"),
        _field(0, 0, "desc", "params", "0 format codes, 0 parameters, 0 result formats"),
    ], "yellow", "Bind parameters to the prepared statement. Creates a portal ready to execute.")

    describe_msg = b"D" + struct.pack("!i", 4 + 1 + 1) + b"P" + b"\x00"
    _show_bytes(p, "CLIENT →", "Describe", describe_msg, [
        _field(0, 1, "byte", "type", "'D' (0x44)"),
        _field(1, 4, "int32", "length", 6),
        _field(5, 1, "byte", "target", "'P' = portal"),
        _field(6, 1, "string", "name", "(unnamed)"),
    ], "yellow", "Ask server to describe the portal's output columns.")

    execute_msg = b"E" + struct.pack("!i", 4 + 1 + 4) + b"\x00" + struct.pack("!i", 0)
    _show_bytes(p, "CLIENT →", "Execute", execute_msg, [
        _field(0, 1, "byte", "type", "'E' (0x45)"),
        _field(1, 4, "int32", "length", 9),
        _field(5, 1, "string", "portal", "(unnamed)"),
        _field(6, 4, "int32", "max_rows", "0 = no limit"),
    ], "yellow", "Execute the portal. Returns all rows.")

    sync_msg = b"S" + struct.pack("!i", 4)
    _show_bytes(p, "CLIENT →", "Sync", sync_msg, [
        _field(0, 1, "byte", "type", "'S' (0x53)"),
        _field(1, 4, "int32", "length", 4),
    ], "yellow", "Sync — tells server to process all pending messages and send ReadyForQuery.")

    sock.sendall(parse_msg + bind_msg + describe_msg + execute_msg + sync_msg)


def _parse_parameter_status(payload):
    parts = payload.decode("utf-8").split("\x00")
    return parts[0], parts[1] if len(parts) > 1 else ""


def _parse_row_description(payload):
    ncols = struct.unpack("!h", payload[:2])[0]
    offset = 2
    cols = []
    for _ in range(ncols):
        end = payload.index(b"\x00", offset)
        name = payload[offset:end].decode()
        offset = end + 1
        table_oid, col_num, type_oid, size, type_mod, fmt = struct.unpack("!ihihih", payload[offset:offset + 18])
        offset += 18
        cols.append({"name": name, "type_oid": type_oid, "size": size})
    return cols


def _parse_data_row(payload):
    ncols = struct.unpack("!h", payload[:2])[0]
    offset = 2
    values = []
    for _ in range(ncols):
        length = struct.unpack("!i", payload[offset:offset + 4])[0]
        offset += 4
        if length == -1:
            values.append(None)
        else:
            values.append(payload[offset:offset + length].decode())
            offset += length
    return values


def _parse_error(payload):
    fields = {}
    i = 0
    while i < len(payload) and payload[i] != 0:
        field_type = chr(payload[i])
        i += 1
        end = payload.index(b"\x00", i)
        value = payload[i:end].decode()
        i = end + 1
        fields[field_type] = value
    severity = fields.get("S", "ERROR")
    message = fields.get("M", "unknown error")
    return f"{severity}: {message}"


def _parse_dsn(dsn):
    result = {}
    if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
        from urllib.parse import urlparse
        parsed = urlparse(dsn)
        if parsed.hostname:
            result["host"] = parsed.hostname
        if parsed.port:
            result["port"] = str(parsed.port)
        if parsed.username:
            result["user"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        if parsed.path and parsed.path != "/":
            result["database"] = parsed.path.lstrip("/")
    return result
