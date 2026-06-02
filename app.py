import json
import base64
import requests
import urllib3
import gzip
import os
import urllib.parse
from datetime import datetime
from collections import OrderedDict
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
try:
    import blackboxprotobuf
except ImportError:
    import bbpb as blackboxprotobuf

urllib3.disable_warnings()

app = Flask(__name__)
CORS(app)
app.json.sort_keys = False

import traceback as _tb
_debug_log = []

def _add_log(success, message, details=None):
    _debug_log.append({
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'success': success,
        'message': message,
        'details': details or ''
    })
    if len(_debug_log) > 100:
        _debug_log.pop(0)

@app.route('/debug')
def debug():
    return jsonify(_debug_log)

@app.route('/health')
def health():
    modules = {
        'my_pb2': False,
        'output_pb2': False,
        'jwt': False,
        'bbpb': False
    }
    try:
        import my_pb2
        modules['my_pb2'] = True
    except: pass
    try:
        import output_pb2
        modules['output_pb2'] = True
    except: pass
    try:
        import jwt
        modules['jwt'] = True
    except: pass
    try:
        import blackboxprotobuf
        modules['bbpb'] = True
    except: pass
    return jsonify({'modules': modules})

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV  = b'6oyZDr22E3ychjM%'

def enc(data: bytes) -> bytes:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(data, AES.block_size))

def dec(data: bytes) -> bytes:
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        return unpad(cipher.decrypt(data), AES.block_size)
    except Exception:
        return data

GAME_HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; SM-S908E Build/TP1A.220624.014)",
    "X-GA": "v1 1",
    "X-Unity-Version": "2018.4.11f1",
    "ReleaseVersion": "OB53",
    "Content-Type": "application/octet-stream",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
}

FRIEND_HEADERS = {
    "Authorization": None,
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "UnityPlayer/2022.3.47f1",
    "ReleaseVersion": "OB53",
    "X-Unity-Version": "2022.3.47f1",
    "X-GA": "v1 1"
}

def smart_encode(payload_dict: dict) -> bytes:
    typedef = {}
    for key, value in payload_dict.items():
        if isinstance(value, int):
            typedef[str(key)] = {'type': 'int', 'name': ''}
        elif isinstance(value, str):
            typedef[str(key)] = {'type': 'bytes', 'name': ''}
        elif isinstance(value, dict):
            _, inner_typedef = blackboxprotobuf.decode_message(
                blackboxprotobuf.encode_message(value, {})
            )
            typedef[str(key)] = {'type': 'message', 'message_typedef': inner_typedef, 'name': ''}
        else:
            typedef[str(key)] = {'type': 'int', 'name': ''}
    return blackboxprotobuf.encode_message(payload_dict, typedef)

def parse_protobuf_raw(data: bytes) -> dict:
    result = {}
    pos = 0
    def read_varint():
        nonlocal pos
        value = 0
        shift = 0
        while pos < len(data):
            byte_val = data[pos]
            pos += 1
            value |= (byte_val & 0x7F) << shift
            if not (byte_val & 0x80):
                break
            shift += 7
        return value
    while pos < len(data):
        try:
            first_byte = data[pos]
            pos += 1
            field_number = first_byte >> 3
            wire_type = first_byte & 0x07
            if wire_type == 0:
                value = read_varint()
                result.setdefault(field_number, []).append(value)
            elif wire_type == 2:
                length = read_varint()
                chunk = data[pos:pos+length]
                pos += length
                try:
                    string_value = chunk.decode('utf-8')
                    result.setdefault(field_number, []).append(string_value)
                except:
                    nested = parse_protobuf_raw(chunk)
                    result.setdefault(field_number, []).append(nested)
        except:
            break
    for k, v in list(result.items()):
        if isinstance(v, list) and len(v) == 1:
            result[k] = v[0]
    return result

def get_complete_friends_json(token: str) -> OrderedDict:
    headers = FRIEND_HEADERS.copy()
    headers["Authorization"] = f"Bearer {token}"
    payload = b'\x59\x8F\xCA\xF0\x78\x39\x30\x8F\xF2\x87\xAC\xA3\xAE\x0A\x06\x17'
    try:
        resp = requests.post(
            "https://clientbp.ggpolarbear.com/GetFriend",
            headers=headers,
            data=payload,
            timeout=10
        )
        raw = resp.content
        parsed = parse_protobuf_raw(raw)
        friends_summary = []
        raw_list = parsed.get(1, [])
        if isinstance(raw_list, dict):
            raw_list = [raw_list]
        elif not isinstance(raw_list, list):
            raw_list = []
        def clean_uid(value):
            if isinstance(value, int):
                return value
            if isinstance(value, list):
                for item in value:
                    res = clean_uid(item)
                    if res is not None:
                        return res
                return None
            if isinstance(value, dict):
                for v in value.values():
                    res = clean_uid(v)
                    if res is not None:
                        return res
                return None
            return None

        def clean_name(value):
            if isinstance(value, str) and len(value) > 0:
                if any(ord(c) < 32 and c not in ('\n', '\r', '\t') for c in value):
                    return None
                return value
            if isinstance(value, list):
                for item in value:
                    res = clean_name(item)
                    if res:
                        return res
                return None
            return None

        for f in raw_list:
            if isinstance(f, dict):
                raw_uid = f.get(1, None)
                raw_name = f.get(3, None)
                raw_level = f.get(8, "Unknown")
                raw_region = f.get(6, "Unknown")
                if isinstance(raw_region, list) and raw_region:
                    raw_region = raw_region[0]
                uid = clean_uid(raw_uid) or "Unknown"
                name = clean_name(raw_name) or "Unknown"
                level = raw_level if isinstance(raw_level, int) else "Unknown"
                friend_obj = OrderedDict([
                    ("name", name),
                    ("uid", uid),
                    ("level", level),
                    ("region", raw_region)
                ])
                friends_summary.append(friend_obj)

        self_player = None
        if friends_summary:
            self_player = friends_summary.pop()

        return OrderedDict([
            ("success", True),
            ("status_code", resp.status_code),
            ("total_found", len(friends_summary)),
            ("friends", friends_summary),
            ("self_player", self_player)
        ])
    except Exception as e:
        return OrderedDict([
            ("success", False),
            ("error", str(e))
        ])

def send_encrypted_request(endpoint: str, payload_dict: dict, jwt: str) -> dict:
    headers = GAME_HEADERS.copy()
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}" if not jwt.startswith("Bearer ") else jwt
    try:
        binary_payload = smart_encode(payload_dict)
        encrypted_req = enc(binary_payload)
        r = requests.post(endpoint, headers=headers, data=encrypted_req, timeout=15, verify=False)
        decrypted = dec(r.content)
        if decrypted.startswith(b'\x1f\x8b'):
            try:
                decrypted = gzip.decompress(decrypted)
            except:
                pass
        try:
            decoded_msg, _ = blackboxprotobuf.decode_message(decrypted)
            decoded_text = json.dumps(decoded_msg, indent=2, ensure_ascii=False)
        except Exception:
            try:
                decoded_text = decrypted.decode('utf-8')
            except UnicodeDecodeError:
                decoded_text = f"<binary, {len(decrypted)} bytes, hex: {decrypted.hex()}>"
        return {
            "status_code": r.status_code,
            "decoded_body": decoded_text,
            "is_gzip": decrypted != r.content and r.content[:2] != b'\x1f\x8b'
        }
    except Exception as e:
        return {"status_code": 0, "decoded_body": str(e), "is_gzip": False}

def clean_bytes_for_json(data):
    if isinstance(data, dict):
        return {k: clean_bytes_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_bytes_for_json(v) for v in data]
    elif isinstance(data, bytes):
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return data.hex()
    return data

def decode_ff_name(b64_str):
    try:
        key = b"1e5898ccb8dfdd921f9bdea848768b64a201"
        b64_str = b64_str.strip()
        b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
        encrypted_bytes = base64.b64decode(b64_str)
        decrypted_bytes = bytearray()
        for i, byte in enumerate(encrypted_bytes):
            key_byte = key[i % len(key)]
            decrypted_bytes.append(byte ^ key_byte)
        return decrypted_bytes.decode('utf-8', errors='ignore')
    except Exception:
        return b64_str

def eat_to_access_token(eat_input):
    eat_token = eat_input
    if "http" in eat_input or "?" in eat_input:
        parsed_url = urllib.parse.urlparse(eat_input)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        eat_token = query_params.get('eat', [None])[0]
    if not eat_token:
        return {"success": False, "message": "Invalid EAT format"}
    api_url = f"https://api-otrss.garena.com/support/callback/?access_token={eat_token}"
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/114.0.0.0 Mobile"}
    try:
        response = requests.get(api_url, headers=headers, allow_redirects=True, timeout=10)
        final_params = urllib.parse.parse_qs(urllib.parse.urlparse(response.url).query)
        if 'access_token' in final_params:
            return {
                "success": True,
                "access_token": final_params['access_token'][0],
                "account_id": final_params.get('account_id', ['Unknown'])[0],
                "nickname": urllib.parse.unquote(final_params.get('nickname', ['Unknown'])[0]),
                "region": final_params.get('region', ['Unknown'])[0]
            }
        return {"success": False, "message": "Failed to get Access Token from EAT."}
    except Exception as e:
        return {"success": False, "message": str(e)}

def fetch_open_id(access_token):
    try:
        uid_url = "https://prod-api.reward.ff.garena.com/redemption/api/auth/inspect_token/"
        uid_headers = {
            "authority": "prod-api.reward.ff.garena.com",
            "method": "GET",
            "path": "/redemption/api/auth/inspect_token/",
            "scheme": "https",
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "access-token": access_token,
            "cookie": "_gid=GA1.2.444482899.1724033242; _ga_XB5PSHEQB4=GS1.1.1724040177.1.1.1724040732.0.0.0; token_session=cb73a97aaef2f1c7fd138757dc28a08f92904b1062e66c; _ga_KE3SY7MRSD=GS1.1.1724041788.0.0.1724041788.0; _ga_RF9R6YT614=GS1.1.1724041788.0.0.1724041788.0; _ga=GA1.1.1843180339.1724033241; apple_state_key=817771465df611ef8ab00ac8aa985783; _ga_G8QGMJPWWV=GS1.1.1724049483.1.1.1724049880.0.0; datadome=HBTqAUPVsbBJaOLirZCUkN3rXjf4gRnrZcNlw2WXTg7bn083SPey8X~ffVwr7qhtg8154634Ee9qq4bCkizBuiMZ3Qtqyf3Isxmsz6GTH_b6LMCKWF4Uea_HSPk;",
            "origin": "https://reward.ff.garena.com",
            "referer": "https://reward.ff.garena.com/",
            "sec-ch-ua": '"Not.A/Brand";v="99", "Chromium";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        uid_res = requests.get(uid_url, headers=uid_headers, verify=False, timeout=10)
        uid_data = uid_res.json()
        uid = uid_data.get("uid")
        if not uid:
            print(f"[fetch_open_id] No UID in response: {uid_data}")
            return None

        openid_url = "https://topup.pk/api/auth/player_id_login"
        openid_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-MM,en-US;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": "https://topup.pk",
            "Referer": "https://topup.pk/",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Android WebView";v="138"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Linux; Android 15; RMX5070 Build/UKQ1.231108.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.7204.157 Mobile Safari/537.36",
            "X-Requested-With": "mark.via.gp",
            "Cookie": "source=mb; region=PK; mspid2=13c49fb51ece78886ebf7108a4907756; _fbp=fb.1.1753985808817.794945392376454660; language=en; datadome=WQaG3HalUB3PsGoSXY3TdcrSQextsSFwkOp1cqZtJ7Ax4YkiERHUgkgHlEAIccQO~w8dzTGM70D9SzaH7vymmEqOrVeX5pIsPVE22Uf3TDu6W3WG7j36ulnTg2DltRO7; session_key=hq02g63z3zjcumm76mafcooitj7nc79y",
        }
        payload = {"app_id": 100067, "login_id": str(uid)}
        openid_res = requests.post(openid_url, headers=openid_headers, json=payload, verify=False, timeout=10)
        openid_data = openid_res.json()
        open_id = openid_data.get("open_id")
        if not open_id:
            print(f"[fetch_open_id] No open_id in response: {openid_data}")
            return None
        return open_id
    except Exception as e:
        print(f"[fetch_open_id] Exception: {e}")
        return None

def perform_majorlogin(access_token, open_id):
    """
    Manually encode the MajorLogin protobuf request, try multiple platform IDs,
    parse the response without my_pb2/output_pb2.
    """
    def _vr(n):
        h = []
        while True:
            b = n & 0x7F; n >>= 7
            if n: b |= 0x80
            h.append(b)
            if not n: break
        return bytes(h)

    def _var(fn, val):
        return _vr((fn << 3) | 0) + _vr(val)

    def _len(fn, val):
        e = val.encode() if isinstance(val, str) else val
        return _vr((fn << 3) | 2) + _vr(len(e)) + e

    def _pb(flds):
        p = bytearray()
        for f, v in flds.items():
            if isinstance(v, int):
                p.extend(_var(f, v))
            elif isinstance(v, (str, bytes)):
                p.extend(_len(f, v))
        return p

    platforms = [8, 3, 4, 6]
    url = "https://loginbp.ggpolarbear.com/MajorLogin"
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/octet-stream",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB53"
    }

    for platform_type in platforms:
        game_data = {
            3: "2024-12-05 18:15:32",
            4: "free fire",
            5: 1,
            7: "1.108.3",
            8: "Android OS 9 / API-28 (PI/rel.cjw.20220518.114133)",
            9: "Handheld",
            10: "Verizon Wireless",
            11: "WIFI",
            12: 1280, 13: 960,
            14: "240",
            15: "ARMv7 VFPv3 NEON VMH | 2400 | 4",
            16: 5951,
            17: "Adreno (TM) 640",
            18: "OpenGL ES 3.0",
            19: "Google|74b585a9-0268-4ad3-8f36-ef41d2e53610",
            20: "172.190.111.97",
            21: "en",
            22: open_id,
            29: access_token,
            30: platform_type,
            99: str(platform_type),
            100: str(platform_type),
        }
        serialized = _pb(game_data)
        encrypted_payload = enc(serialized)

        try:
            resp = requests.post(url, data=encrypted_payload, headers=headers, verify=False, timeout=5)
            if resp.status_code == 200:
                raw = parse_protobuf_raw(resp.content)
                token = raw.get(8)
                if isinstance(token, list) and token:
                    token = token[0]
                if isinstance(token, bytes):
                    token = token.decode()
                if token:
                    try:
                        import jwt as pyjwt
                        decoded = pyjwt.decode(token, options={"verify_signature": False})
                    except Exception:
                        payload_b64 = token.split('.')[1]
                        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                        decoded = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))

                    raw_nickname = decoded.get("nickname", "")
                    account_name = decode_ff_name(raw_nickname)
                    if "Error decoding" in account_name or not account_name:
                        account_name = urllib.parse.unquote(raw_nickname)

                    return {
                        "success": True,
                        "jwt_token": token,
                        "account_id": decoded.get("account_id"),
                        "account_name": account_name,
                        "platform": str(decoded.get("external_type")),
                        "region": decoded.get("lock_region")
                    }
        except Exception:
            continue

    return {"success": False, "message": "No valid platform found or all authentication attempts failed."}

def access_token_to_jwt(access_token, manual_open_id=None):
    open_id = manual_open_id or fetch_open_id(access_token)
    if not open_id:
        return {"success": False, "message": "Could not extract Open ID"}
    return perform_majorlogin(access_token, open_id)

def guest_login_to_jwt(uid, password):
    oauth_url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    payload = {
        'uid': uid,
        'password': password,
        'response_type': "token",
        'client_type': "2",
        'client_secret': "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        'client_id': "100067"
    }
    headers = {
        'User-Agent': "GarenaMSDK/4.0.19P9(SM-M526B ;Android 13;pt;BR;)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip"
    }

    try:
        oauth_response = requests.post(oauth_url, data=payload, headers=headers, timeout=10)
    except requests.RequestException as e:
        return {"success": False, "message": f"Guest login connection failed: {str(e)}"}

    if oauth_response.status_code != 200:
        try:
            error_json = oauth_response.json()
        except ValueError:
            error_json = {"error": oauth_response.text}
        return {"success": False, "message": f"OAuth error {oauth_response.status_code}: {error_json}"}

    try:
        oauth_data = oauth_response.json()
    except ValueError:
        return {"success": False, "message": "Invalid JSON response from OAuth service"}

    if 'access_token' not in oauth_data or 'open_id' not in oauth_data:
        return {"success": False, "message": "OAuth response missing access_token or open_id"}

    access_token = oauth_data['access_token']
    open_id = oauth_data['open_id']

    jwt_result = perform_majorlogin(access_token, open_id)
    if jwt_result.get('success'):
        jwt_result['access_token'] = access_token
    return jwt_result

@app.route('/convert/access', methods=['GET'])
def convert_access_token():
    access_token = request.args.get('access_token', '').strip()
    open_id = request.args.get('open_id', '').strip() or None
    if not access_token:
        _add_log(False, "ConvertAccess missing token")
        return jsonify({'success': False, 'error': 'access_token required'}), 400
    try:
        result = access_token_to_jwt(access_token, manual_open_id=open_id)
        if result.get('success'):
            _add_log(True, "Access token converted to JWT")
            return jsonify({'success': True, 'jwt': result.get('jwt_token'), 'data': result})
        else:
            _add_log(False, f"Access conversion failed: {result.get('message')}")
            return jsonify({'success': False, 'error': result.get('message')}), 400
    except Exception as e:
        _add_log(False, f"ConvertAccess exception: {str(e)}", _tb.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/convert/guest', methods=['GET'])
def convert_guest():
    uid = request.args.get('uid', '').strip()
    password = request.args.get('password', '').strip()
    if not uid or not password:
        _add_log(False, "ConvertGuest missing uid/password")
        return jsonify({'success': False, 'error': 'uid and password required'}), 400
    try:
        result = guest_login_to_jwt(uid, password)
        if result.get('success'):
            _add_log(True, "Guest login converted to JWT")
            return jsonify({'success': True, 'jwt': result.get('jwt_token'), 'data': result})
        else:
            _add_log(False, f"Guest conversion failed: {result.get('message')}")
            return jsonify({'success': False, 'error': result.get('message')}), 400
    except Exception as e:
        _add_log(False, f"ConvertGuest exception: {str(e)}", _tb.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/convert/eat', methods=['GET'])
def convert_eat():
    eat_token = request.args.get('eat_token', '').strip()
    if not eat_token:
        _add_log(False, "ConvertEAT missing token")
        return jsonify({'success': False, 'error': 'eat_token required'}), 400
    try:
        eat_result = eat_to_access_token(eat_token)
        if not eat_result.get('success'):
            _add_log(False, f"EAT conversion failed: {eat_result.get('message')}")
            return jsonify({'success': False, 'error': eat_result.get('message')}), 400
        access_token = eat_result.get('access_token')
        jwt_result = access_token_to_jwt(access_token)
        if jwt_result.get('success'):
            jwt_result['access_token'] = access_token
            _add_log(True, "EAT token converted to JWT")
            return jsonify({'success': True, 'jwt': jwt_result.get('jwt_token'), 'data': jwt_result})
        else:
            _add_log(False, f"EAT->JWT failed: {jwt_result.get('message')}")
            return jsonify({'success': False, 'error': jwt_result.get('message')}), 400
    except Exception as e:
        _add_log(False, f"ConvertEAT exception: {str(e)}", _tb.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/get", methods=["GET"])
def get_friends():
    jwt = request.args.get("jwt")
    if not jwt:
        _add_log(False, "Missing JWT")
        return jsonify(OrderedDict([("success", False), ("error", "JWT token is required")])), 400
    try:
        result = get_complete_friends_json(jwt)
        _add_log(result.get("success", True), f"GetFriends success={result.get('success')}")
        return jsonify(result)
    except Exception as e:
        _add_log(False, f"GetFriends error: {str(e)}", _tb.format_exc())
        return jsonify(OrderedDict([("success", False), ("error", str(e))])), 500

@app.route("/add", methods=["GET"])
def add_friend():
    uid = request.args.get("uid")
    jwt = request.args.get("jwt")
    if not uid or not jwt:
        _add_log(False, "AddFriend missing uid/jwt")
        return jsonify(OrderedDict([("success", False), ("error", "Both 'uid' and 'jwt' are required")])), 400
    try:
        uid_int = int(uid)
    except ValueError:
        _add_log(False, "AddFriend invalid UID")
        return jsonify(OrderedDict([("success", False), ("error", "UID must be an integer")])), 400
    payload = {"1": 15618305639, "2": uid_int, "3": 24, "4": 8}
    try:
        result = send_encrypted_request(
            "https://clientbp.ggpolarbear.com/RequestAddingFriend", payload, jwt
        )
        status = result["status_code"]
        if status == 200:
            _add_log(True, f"Friend request sent to {uid}")
            return jsonify(OrderedDict([("success", True), ("message", "Friend request sent successfully")]))
        else:
            msg = f"AddFriend failed (status {status})"
            _add_log(False, msg, result.get("decoded_body", ""))
            return jsonify(OrderedDict([("success", False), ("status_code", status), ("raw_response", result["decoded_body"])]))
    except Exception as e:
        _add_log(False, f"AddFriend exception: {str(e)}", _tb.format_exc())
        return jsonify(OrderedDict([("success", False), ("error", str(e))])), 500

@app.route("/remove", methods=["GET"])
def remove_friend():
    uid = request.args.get("uid")
    jwt = request.args.get("jwt")
    if not uid or not jwt:
        _add_log(False, "RemoveFriend missing uid/jwt")
        return jsonify(OrderedDict([("success", False), ("error", "Both 'uid' and 'jwt' are required")])), 400
    try:
        uid_int = int(uid)
    except ValueError:
        _add_log(False, "RemoveFriend invalid UID")
        return jsonify(OrderedDict([("success", False), ("error", "UID must be an integer")])), 400
    payload = {"1": 15618305639, "2": uid_int}
    try:
        result = send_encrypted_request(
            "https://clientbp.ggpolarbear.com/RemoveFriend", payload, jwt
        )
        status = result["status_code"]
        if status == 200:
            _add_log(True, f"Friend removed {uid}")
            return jsonify(OrderedDict([("success", True), ("message", "Friend removed successfully")]))
        else:
            _add_log(False, f"RemoveFriend failed (status {status})", result.get("decoded_body", ""))
            return jsonify(OrderedDict([("success", False), ("status_code", status), ("raw_response", result["decoded_body"])]))
    except Exception as e:
        _add_log(False, f"RemoveFriend exception: {str(e)}", _tb.format_exc())
        return jsonify(OrderedDict([("success", False), ("error", str(e))])), 500

@app.route('/req-list', methods=['GET'])
def get_friend_requests():
    jwt = request.args.get('jwt', '').strip()
    if not jwt:
        _add_log(False, "ReqList missing JWT")
        return jsonify(OrderedDict([("status", "failed"), ("http_code", 400), ("error_message", "Missing 'jwt' parameter")])), 400
    url = "https://clientbp.polarbear.com/GetFriendRequestList"
    payload_dict = {"3": {}}
    headers = GAME_HEADERS.copy()
    headers['Authorization'] = jwt if jwt.startswith("Bearer") else f"Bearer {jwt}"
    try:
        binary_payload = smart_encode(payload_dict)
        encrypted_req = enc(binary_payload)
        r = requests.post(url, headers=headers, data=encrypted_req, timeout=15, verify=False)
        if r.status_code != 200:
            _add_log(False, f"ReqList API error {r.status_code}")
            return jsonify(OrderedDict([("status", "failed"), ("http_code", r.status_code), ("error_message", "API returned an error.")])), r.status_code
        decrypted = dec(r.content)
        if decrypted.startswith(b'\x1f\x8b'):
            try:
                decrypted = gzip.decompress(decrypted)
            except Exception as e:
                _add_log(False, f"ReqList gzip error: {str(e)}", _tb.format_exc())
                return jsonify(OrderedDict([("status", "failed"), ("http_code", 500), ("error_message", f"Gzip Decompression failed: {str(e)}")])), 500
        if len(decrypted) < 3:
            _add_log(True, "ReqList empty – returning 0 requests")
            return jsonify(OrderedDict([
                ("status", "success"),
                ("http_code", r.status_code),
                ("total_requests", 0),
                ("requests", [])
            ]))
        decoded_dict, _ = blackboxprotobuf.decode_message(decrypted)
        clean_json = clean_bytes_for_json(decoded_dict)
        parsed_requests = []
        raw_list_data = clean_json.get("1", {}).get("1", [])
        if isinstance(raw_list_data, dict):
            raw_list_data = [raw_list_data]
        for player in raw_list_data:
            player_info = OrderedDict([
                ("nickname", player.get("3", "Unknown")),
                ("uid", player.get("1", "Unknown")),
                ("level", player.get("6", 0)),
            ])
            parsed_requests.append(player_info)
        _add_log(True, f"ReqList returned {len(parsed_requests)} requests")
        return jsonify(OrderedDict([
            ("status", "success"),
            ("http_code", r.status_code),
            ("total_requests", len(parsed_requests)),
            ("requests", parsed_requests)
        ]))
    except Exception as e:
        _add_log(False, f"ReqList exception: {str(e)}", _tb.format_exc())
        return jsonify(OrderedDict([("status", "failed"), ("http_code", 500), ("error_message", str(e))])), 500

@app.route('/accept', methods=['GET'])
def accept_friend():
    uid = request.args.get('uid')
    jwt = request.args.get('jwt')
    if not uid or not jwt:
        _add_log(False, "Accept missing uid/jwt")
        return jsonify(OrderedDict([("success", False), ("error", "Missing uid or jwt")])), 400
    try:
        uid_int = int(uid)
    except ValueError:
        _add_log(False, "Accept invalid UID")
        return jsonify(OrderedDict([("success", False), ("error", "UID must be a number")])), 400
    url = "https://clientbp.polarbear.com/ConfirmFriendRequest"
    headers = GAME_HEADERS.copy()
    headers['Authorization'] = jwt if jwt.startswith("Bearer") else f"Bearer {jwt}"
    try:
        binary_payload = smart_encode({"1": uid_int})
        encrypted_req = enc(binary_payload)
        r = requests.post(url, headers=headers, data=encrypted_req, timeout=15, verify=False)
        if r.status_code != 200:
            _add_log(False, f"Accept API error {r.status_code}")
            return jsonify(OrderedDict([("success", False), ("status", r.status_code), ("error", "API returned error")])), r.status_code
        decrypted = dec(r.content)
        if decrypted.startswith(b'\x1f\x8b'):
            try:
                decrypted = gzip.decompress(decrypted)
            except:
                pass
        decoded_dict, _ = blackboxprotobuf.decode_message(decrypted)
        clean_dict = clean_bytes_for_json(decoded_dict)
        _add_log(True, f"Friend request accepted {uid}")
        return jsonify(OrderedDict([
            ("success", True),
            ("message", "Friend request accepted! 🎉")
        ]))
    except Exception as e:
        _add_log(False, f"Accept exception: {str(e)}", _tb.format_exc())
        return jsonify(OrderedDict([("success", False), ("error", str(e))])), 500

@app.route('/reject', methods=['GET'])
def reject_friend():
    uid = request.args.get('uid')
    jwt = request.args.get('jwt')
    if not uid or not jwt:
        _add_log(False, "Reject missing uid/jwt")
        return jsonify(OrderedDict([("success", False), ("error", "Missing uid or jwt")])), 400
    try:
        uid_int = int(uid)
    except ValueError:
        _add_log(False, "Reject invalid UID")
        return jsonify(OrderedDict([("success", False), ("error", "UID must be a number")])), 400
    url = "https://clientbp.polarbear.com/DeclineFriendRequest"
    headers = GAME_HEADERS.copy()
    headers['Authorization'] = jwt if jwt.startswith("Bearer") else f"Bearer {jwt}"
    try:
        binary_payload = smart_encode({"1": uid_int})
        encrypted_req = enc(binary_payload)
        r = requests.post(url, headers=headers, data=encrypted_req, timeout=15, verify=False)
        if r.status_code != 200:
            _add_log(False, f"Reject API error {r.status_code}")
            return jsonify(OrderedDict([("success", False), ("status", r.status_code), ("error", "API returned error")])), r.status_code
        _add_log(True, f"Friend request rejected {uid}")
        return jsonify(OrderedDict([("success", True), ("message", f"Friend request rejected.")]))
    except Exception as e:
        _add_log(False, f"Reject exception: {str(e)}", _tb.format_exc())
        return jsonify(OrderedDict([("success", False), ("error", str(e))])), 500

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Free Fire Friends Manager</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <link href="https://unpkg.com/aos@2.3.1/dist/aos.css" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #01000E;
            --card-bg: rgba(12, 5, 50, 0.4);
            --border-color: rgba(167, 139, 250, 0.2);
            --glow-color: rgba(192, 132, 252, 0.6);
            --text-glow: #e0d5ff;
        }
        html { scroll-behavior: smooth; overflow-x: hidden; }
        body {
            font-family: 'Poppins', sans-serif;
            background-color: var(--bg-dark);
            color: #d9d2ff;
            overflow-x: hidden;
            width: 100%;
            margin: 0; padding: 0;
            overflow-y: auto;
            -ms-overflow-style: none; scrollbar-width: none;
        }
        body::-webkit-scrollbar { display: none; }
        #vanta-bg { position: fixed; width: 100%; height: 100%; top: 0; left: 0; z-index: -1; pointer-events: none; }
        .gradient-text {
            background: linear-gradient(90deg, #c7d2fe, #fbcfe8, #c7d2fe);
            background-size: 200% auto;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: text-shimmer 4s linear infinite;
        }
        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes text-shimmer { to { background-position: 200% center; } }
        .glass-card {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid var(--border-color);
            border-radius: 1.5rem;
            transition: all 0.5s cubic-bezier(0.25, 0.8, 0.25, 1);
        }
        .btn-glow {
            background: linear-gradient(90deg, #9333ea, #4f46e5);
            color: white; border: none; cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 0 15px rgba(139, 92, 246, 0.4);
            padding: 0.75rem 1.5rem; border-radius: 0.75rem; font-weight: 600;
            display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem;
        }
        .btn-glow:hover { box-shadow: 0 0 30px rgba(167, 139, 250, 0.8); transform: scale(1.02); }
        .btn-outline {
            background: transparent; border: 1px solid rgba(239, 68, 68, 0.5);
            color: #f87171; padding: 0.4rem 1rem; border-radius: 0.5rem;
            font-weight: 600; transition: all 0.2s; display: inline-flex; align-items: center; gap: 0.3rem;
        }
        .btn-outline:hover { background: rgba(239, 68, 68, 0.15); }
        .btn-accept {
            background: rgba(16, 185, 129, 0.2); border: 1px solid rgba(16, 185, 129, 0.5);
            color: #34d399; padding: 0.4rem 1rem; border-radius: 0.5rem;
            font-weight: 600; transition: all 0.2s; display: inline-flex; align-items: center; gap: 0.3rem;
        }
        .btn-accept:hover { background: rgba(16, 185, 129, 0.3); }
        .btn-reject {
            background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.5);
            color: #f87171; padding: 0.4rem 1rem; border-radius: 0.5rem;
            font-weight: 600; transition: all 0.2s; display: inline-flex; align-items: center; gap: 0.3rem;
        }
        .btn-reject:hover { background: rgba(239, 68, 68, 0.25); }
        .form-input {
            background: rgba(12, 5, 50, 0.5);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            color: white; padding: 0.75rem 1rem;
            width: 100%; outline: none; transition: 0.3s; font-size: 0.875rem;
            box-sizing: border-box; font-family: 'Poppins', sans-serif;
        }
        .form-input:focus { border-color: var(--glow-color); box-shadow: 0 0 15px var(--glow-color); }
        .tab-btn {
            padding: 10px 20px; color: #a78bfa; border-radius: 0.75rem; font-weight: 600;
            cursor: pointer; transition: all 0.3s; background: transparent; border: 1px solid transparent;
        }
        .tab-btn.active {
            background: rgba(147, 51, 234, 0.2); color: white;
            border: 1px solid rgba(147, 51, 234, 0.6); box-shadow: 0 0 15px rgba(192, 132, 252, 0.3);
        }
        .tab-pane { display: none; }
        .tab-pane.active { display: block; }
        .text-glow { text-shadow: 0 0 10px var(--text-glow); }

        /* popup */
        .popup-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(10px);
            z-index: 200; display: flex; align-items: center; justify-content: center;
            animation: popupFadeIn 0.25s ease;
        }
        @keyframes popupFadeIn { from { opacity: 0; } to { opacity: 1; } }
        .popup-card {
            background: rgba(20, 10, 60, 0.9); backdrop-filter: blur(25px);
            border: 1px solid rgba(167, 139, 250, 0.3); border-radius: 1.5rem;
            padding: 2rem; max-width: 400px; width: 90%; text-align: center;
            box-shadow: 0 0 40px rgba(147, 51, 234, 0.3);
            animation: popupSlideIn 0.3s ease;
        }
        @keyframes popupSlideIn { from { transform: scale(0.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
    </style>
</head>
<body class="antialiased">
    <div id="vanta-bg"></div>

    <div id="toast-container" style="position:fixed; top:1rem; left:50%; transform:translateX(-50%); z-index:300; display:flex; flex-direction:column; gap:0.5rem; pointer-events:none;"></div>

    <header class="fixed top-0 left-0 w-full z-50 glass-card !rounded-none !border-x-0 !border-t-0">
        <div class="container mx-auto px-6 py-4 flex justify-between items-center">
            <span class="text-2xl font-bold gradient-text text-glow">
                <i class="fa-solid fa-compact-disc fa-spin mr-2" style="--fa-animation-duration: 5s;"></i>
                FF Friends Manager
            </span>
        </div>
    </header>

    <main class="relative z-10 pt-28 pb-12 px-4">
        <div class="max-w-3xl mx-auto">

            <div class="flex gap-2 justify-center mb-8">
                <div class="tab-btn active" onclick="switchTab('friends')"><i class="fas fa-user-friends mr-2"></i>Friends</div>
                <div class="tab-btn" onclick="switchTab('requests')"><i class="fas fa-user-plus mr-2"></i>Requests</div>
            </div>

            <div id="friends-tab" class="tab-pane active space-y-6">
                <div class="glass-card p-6" data-aos="fade-up">
                    <h2 class="text-xl font-bold text-purple-300 mb-3"><i class="fas fa-key mr-2"></i>Authentication</h2>
                    <div class="flex gap-2 mb-3">
                        <select id="token-type-friends" class="form-input" onchange="switchTokenTypeFriends()">
                            <option value="jwt" selected>JWT Token</option>
                            <option value="access">Access Token</option>
                            <option value="guest">Guest UID + Password</option>
                            <option value="eat">EAT Token</option>
                        </select>
                    </div>
                    <div id="input-jwt-friends">
                        <input type="text" id="jwt-friends" class="form-input" placeholder="Enter JWT Token">
                    </div>
                    <div id="input-access-friends" style="display:none;">
                        <input type="text" id="access-friends" class="form-input mb-2" placeholder="Access Token">
                        <input type="text" id="openid-friends" class="form-input" placeholder="Open ID (optional)">
                    </div>
                    <div id="input-guest-friends" style="display:none;">
                        <div class="flex gap-2">
                            <input type="text" id="guest-uid-friends" class="form-input w-1/2" placeholder="UID">
                            <input type="text" id="guest-pass-friends" class="form-input w-1/2" placeholder="Password">
                        </div>
                    </div>
                    <div id="input-eat-friends" style="display:none;">
                        <input type="text" id="eat-friends" class="form-input" placeholder="EAT Token or URL">
                    </div>
                    <button onclick="loadFriends()" class="btn-glow w-full mt-4"><i class="fas fa-download mr-2"></i>Get Friends</button>
                </div>

                <div class="glass-card p-6" data-aos="fade-up" data-aos-delay="100">
                    <h2 class="text-xl font-bold text-green-300 mb-3"><i class="fas fa-user-plus mr-2"></i>Send Friend Request</h2>
                    <input type="text" id="add-uid" class="form-input" placeholder="Friend UID">
                    <button onclick="addFriend()" class="btn-glow w-full mt-4" style="background: linear-gradient(90deg, #10b981, #059669);"><i class="fas fa-paper-plane mr-2"></i>Send Request</button>
                    <div id="add-result" class="mt-3 text-sm text-center font-medium" style="display:none;"></div>
                </div>

                <div id="friends-loader" class="hidden flex justify-center"><i class="fas fa-spinner fa-spin text-3xl text-purple-400"></i></div>
                <div id="friends-list-container" style="display:none;">
                    <div id="self-player-card" class="glass-card p-4 mb-4 hidden" style="border: 1px solid rgba(167,139,250,0.5); background: rgba(147,51,234,0.15);">
                        <div class="flex items-center justify-between">
                            <div>
                                <div class="flex items-center gap-2 mb-1">
                                    <i class="fas fa-user-circle text-2xl text-purple-300"></i>
                                    <span class="font-bold text-purple-200" id="self-name"></span>
                                    <span class="bg-purple-600/40 text-purple-200 text-xs px-2 py-0.5 rounded-full">You</span>
                                </div>
                                <p class="text-sm text-gray-400">UID: <span id="self-uid"></span></p>
                                <p class="text-sm text-gray-400">Level: <span id="self-level"></span> | Region: <span id="self-region"></span> | Version: <span id="self-version"></span></p>
                            </div>
                        </div>
                    </div>
                    <div class="glass-card p-4 mb-4">
                        <input type="text" id="friend-search" class="form-input" placeholder="Search by UID or name" oninput="filterFriends()">
                    </div>
                    <div class="flex justify-center mb-4">
                        <span id="total-friends-display" class="glass-card px-4 py-1.5 text-sm font-semibold text-purple-300 border border-purple-500/30 rounded-full inline-block"></span>
                    </div>
                    <div id="friends-grid" class="grid grid-cols-1 sm:grid-cols-2 gap-4"></div>
                </div>
            </div>

            <div id="requests-tab" class="tab-pane space-y-6">
                <div class="glass-card p-6" data-aos="fade-up">
                    <h2 class="text-xl font-bold text-purple-300 mb-3"><i class="fas fa-key mr-2"></i>Authentication</h2>
                    <div class="flex gap-2 mb-3">
                        <select id="token-type-requests" class="form-input" onchange="switchTokenTypeRequests()">
                            <option value="jwt" selected>JWT Token</option>
                            <option value="access">Access Token</option>
                            <option value="guest">Guest UID + Password</option>
                            <option value="eat">EAT Token</option>
                        </select>
                    </div>
                    <div id="input-jwt-requests">
                        <input type="text" id="jwt-requests" class="form-input" placeholder="Enter JWT Token">
                    </div>
                    <div id="input-access-requests" style="display:none;">
                        <input type="text" id="access-requests" class="form-input mb-2" placeholder="Access Token">
                        <input type="text" id="openid-requests" class="form-input" placeholder="Open ID (optional)">
                    </div>
                    <div id="input-guest-requests" style="display:none;">
                        <div class="flex gap-2">
                            <input type="text" id="guest-uid-requests" class="form-input w-1/2" placeholder="UID">
                            <input type="text" id="guest-pass-requests" class="form-input w-1/2" placeholder="Password">
                        </div>
                    </div>
                    <div id="input-eat-requests" style="display:none;">
                        <input type="text" id="eat-requests" class="form-input" placeholder="EAT Token or URL">
                    </div>
                    <button onclick="loadRequests()" class="btn-glow w-full mt-4"><i class="fas fa-download mr-2"></i>Get Requests</button>
                </div>

                <div id="requests-loader" class="hidden flex justify-center"><i class="fas fa-spinner fa-spin text-3xl text-purple-400"></i></div>
                <div id="requests-list-container" style="display:none;">
                    <div id="request-self-card" class="glass-card p-4 mb-4 hidden" style="border: 1px solid rgba(167,139,250,0.5); background: rgba(147,51,234,0.15);">
                        <div class="flex items-center justify-between">
                            <div>
                                <div class="flex items-center gap-2 mb-1">
                                    <i class="fas fa-user-circle text-2xl text-purple-300"></i>
                                    <span class="font-bold text-purple-200" id="req-self-name"></span>
                                    <span class="bg-purple-600/40 text-purple-200 text-xs px-2 py-0.5 rounded-full">You</span>
                                </div>
                                <p class="text-sm text-gray-400">UID: <span id="req-self-uid"></span></p>
                                <p class="text-sm text-gray-400">Region: <span id="req-self-region"></span> | Version: <span id="req-self-version"></span></p>
                            </div>
                        </div>
                    </div>
                    <div class="glass-card p-4 mb-4">
                        <input type="text" id="request-search" class="form-input" placeholder="Search by UID or name" oninput="filterRequests()">
                    </div>
                    <div class="flex justify-center mb-4">
                        <span id="total-requests-display" class="glass-card px-4 py-1.5 text-sm font-semibold text-purple-300 border border-purple-500/30 rounded-full inline-block"></span>
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4" id="requests-grid"></div>
                </div>
            </div>
        </div>
    </main>

    <div id="popup-overlay" class="popup-overlay" style="display:none;">
        <div class="popup-card">
            <div id="popup-icon" class="text-4xl mb-3">⚠️</div>
            <div id="popup-title" class="text-xl font-bold text-white mb-2">Confirm Action</div>
            <div id="popup-message" class="text-gray-300 mb-6">Are you sure?</div>
            <div class="flex justify-center gap-4">
                <button id="popup-cancel" class="btn-outline">Cancel</button>
                <button id="popup-confirm" class="btn-glow">Confirm</button>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
    <script src="https://unpkg.com/aos@2.3.1/dist/aos.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r134/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/vanta@latest/dist/vanta.waves.min.js"></script>
    <script>
        let currentFriends = [];
        let currentRequests = [];
        let currentFriendsJwt = null;
        let currentRequestsJwt = null;

        function decodeFFName(b64) {
            try {
                const key = "1e5898ccb8dfdd921f9bdea848768b64a201";
                let str = b64.trim();
                while (str.length % 4) str += "=";
                const enc = Uint8Array.from(atob(str), c => c.charCodeAt(0));
                const dec = new Uint8Array(enc.length);
                for (let i = 0; i < enc.length; i++) {
                    dec[i] = enc[i] ^ key.charCodeAt(i % key.length);
                }
                return new TextDecoder('utf-8').decode(dec);
            } catch (e) {
                return b64;
            }
        }

        function showTopNotification(message, type = 'success') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            const bg = type === 'success'
                ? 'rgba(16, 185, 129, 0.25)'
                : 'rgba(239, 68, 68, 0.25)';
            const border = type === 'success'
                ? '1px solid rgba(16, 185, 129, 0.5)'
                : '1px solid rgba(239, 68, 68, 0.5)';
            const color = type === 'success' ? '#34d399' : '#f87171';
            toast.style.cssText = `
                background: ${bg};
                border: ${border};
                border-radius: 0.75rem;
                padding: 0.75rem 1.5rem;
                color: ${color};
                font-weight: 600;
                backdrop-filter: blur(15px);
                box-shadow: 0 10px 30px rgba(0,0,0,0.3);
                pointer-events: auto;
                animation: slideDown 0.3s ease;
                text-align: center;
                white-space: nowrap;
            `;
            toast.textContent = message;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transition = 'opacity 0.3s';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            event.currentTarget.classList.add('active');
            document.getElementById(tabId + '-tab').classList.add('active');
        }

        function showPopup({ icon, title, message, confirmText = 'Confirm', cancelText = 'Cancel' }) {
            return new Promise((resolve) => {
                document.getElementById('popup-icon').innerText = icon || '⚠️';
                document.getElementById('popup-title').innerText = title;
                document.getElementById('popup-message').innerHTML = message;
                document.getElementById('popup-confirm').innerText = confirmText;
                document.getElementById('popup-cancel').innerText = cancelText;
                const overlay = document.getElementById('popup-overlay');
                overlay.style.display = 'flex';
                const confirmBtn = document.getElementById('popup-confirm');
                const cancelBtn = document.getElementById('popup-cancel');
                function cleanup() {
                    overlay.style.display = 'none';
                    confirmBtn.removeEventListener('click', onConfirm);
                    cancelBtn.removeEventListener('click', onCancel);
                }
                function onConfirm() { cleanup(); resolve(true); }
                function onCancel() { cleanup(); resolve(false); }
                confirmBtn.addEventListener('click', onConfirm);
                cancelBtn.addEventListener('click', onCancel);
            });
        }

        async function loadFriends() {
            const btn = document.querySelector('#friends-tab .btn-glow');
            const origText = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading…';
            btn.disabled = true;

            const type = document.getElementById('token-type-friends').value;
            let jwt = '';

            try {
                if (type === 'jwt') {
                    jwt = document.getElementById('jwt-friends').value.trim();
                } else if (type === 'access') {
                    const access = document.getElementById('access-friends').value.trim();
                    const openid = document.getElementById('openid-friends').value.trim();
                    if (!access) throw new Error('Please enter an Access Token.');
                    const res = await fetch(`/convert/access?access_token=${encodeURIComponent(access)}&open_id=${encodeURIComponent(openid)}`);
                    const data = await res.json();
                    if (data.success) jwt = data.jwt;
                    else throw new Error(data.error || 'Conversion failed');
                } else if (type === 'guest') {
                    const uid = document.getElementById('guest-uid-friends').value.trim();
                    const pass = document.getElementById('guest-pass-friends').value.trim();
                    if (!uid || !pass) throw new Error('Please enter UID and Password.');
                    const res = await fetch(`/convert/guest?uid=${uid}&password=${pass}`);
                    const data = await res.json();
                    if (data.success) jwt = data.jwt;
                    else throw new Error(data.error || 'Conversion failed');
                } else if (type === 'eat') {
                    const eat = document.getElementById('eat-friends').value.trim();
                    if (!eat) throw new Error('Please enter an EAT token or URL.');
                    const res = await fetch(`/convert/eat?eat_token=${encodeURIComponent(eat)}`);
                    const data = await res.json();
                    if (data.success) jwt = data.jwt;
                    else throw new Error(data.error || 'Conversion failed');
                }
            } catch (e) {
                btn.innerHTML = origText;
                btn.disabled = false;
                showTopNotification('Failed to obtain JWT: ' + e.message, 'error');
                return;
            }

            if (!jwt) {
                btn.innerHTML = origText;
                btn.disabled = false;
                showTopNotification('Please enter valid credentials.', 'error');
                return;
            }

            currentFriendsJwt = jwt;

            const res = await fetch(`/get?jwt=${encodeURIComponent(jwt)}`);
            const data = await res.json();
            btn.innerHTML = origText;
            btn.disabled = false;

            if (data.success) {
                currentFriends = data.friends;
                document.getElementById('friends-list-container').style.display = 'block';
                renderFriends(currentFriends);

                const selfCard = document.getElementById('self-player-card');
                if (data.self_player) {
                    document.getElementById('self-name').innerText = data.self_player.name || 'Unknown';
                    document.getElementById('self-uid').innerText = data.self_player.uid || 'Unknown';
                    document.getElementById('self-level').innerText = data.self_player.level || 'Unknown';
                    document.getElementById('self-region').innerText = data.self_player.region || 'Unknown';

                    try {
                        const payload = JSON.parse(atob(currentFriendsJwt.split('.')[1]));
                        document.getElementById('self-version').innerText = payload.release_version || 'Unknown';
                    } catch (e) {
                        document.getElementById('self-version').innerText = 'Unknown';
                    }

                    selfCard.classList.remove('hidden');
                    const y = selfCard.getBoundingClientRect().top + window.scrollY - 90;
                    window.scrollTo({ top: y, behavior: 'smooth' });
                } else {
                    selfCard.classList.add('hidden');
                }

                document.getElementById('total-friends-display').innerText =
                    `Total Friends: ${currentFriends.length}`;
            } else {
                showTopNotification('Failed to load friends: ' + (data.error || 'Unknown error'), 'error');
            }
        }

        function switchTokenTypeFriends() {
            const type = document.getElementById('token-type-friends').value;
            document.getElementById('input-jwt-friends').style.display = type === 'jwt' ? 'block' : 'none';
            document.getElementById('input-access-friends').style.display = type === 'access' ? 'block' : 'none';
            document.getElementById('input-guest-friends').style.display = type === 'guest' ? 'block' : 'none';
            document.getElementById('input-eat-friends').style.display = type === 'eat' ? 'block' : 'none';
        }

        function switchTokenTypeRequests() {
            const type = document.getElementById('token-type-requests').value;
            document.getElementById('input-jwt-requests').style.display = type === 'jwt' ? 'block' : 'none';
            document.getElementById('input-access-requests').style.display = type === 'access' ? 'block' : 'none';
            document.getElementById('input-guest-requests').style.display = type === 'guest' ? 'block' : 'none';
            document.getElementById('input-eat-requests').style.display = type === 'eat' ? 'block' : 'none';
        }

        function renderFriends(friends) {
            const grid = document.getElementById('friends-grid');
            if (friends.length === 0) {
                grid.innerHTML = `
                    <div class="col-span-full glass-card p-8 text-center text-gray-400">
                        <i class="fas fa-user-friends text-4xl mb-3"></i>
                        <p class="text-lg font-semibold">No friends yet</p>
                        <p class="text-sm mt-1">Your friend list is empty. Add some friends to see them here.</p>
                    </div>`;
                return;
            }

            let html = '';
            friends.forEach(f => {
                html += `
                    <div class="glass-card p-4 flex flex-col">
                        <div class="flex justify-between items-start">
                            <div>
                                <h3 class="font-bold text-purple-300">${f.name}</h3>
                                <p class="text-sm text-gray-400">UID: ${f.uid}</p>
                                <p class="text-sm text-gray-400">Level: ${f.level}</p>
                            </div>
                            <button onclick="removeFriend('${f.uid}')" class="btn-outline" title="Remove friend"><i class="fas fa-trash-alt"></i></button>
                        </div>
                    </div>
                `;
            });
            grid.innerHTML = html;
        }

        function filterFriends() {
            const query = document.getElementById('friend-search').value.toLowerCase();
            const filtered = currentFriends.filter(f => 
                f.uid.toString().includes(query) || f.name.toLowerCase().includes(query)
            );
            renderFriends(filtered);
        }

        async function addFriend() {
            const uid = document.getElementById('add-uid').value.trim();
            const jwt = currentFriendsJwt;
            if (!jwt) {
                showTopNotification('Please load your friend list first.', 'error');
                return;
            }
            if (!uid) {
                showAddResult('Please enter a UID.', 'error');
                return;
            }

            const confirmed = await showPopup({
                icon: '📨',
                title: 'Send Friend Request',
                message: `Send a friend request to UID ${uid}?`,
                confirmText: 'Yes, send request',
                cancelText: 'Cancel'
            });
            if (!confirmed) return;

            const res = await fetch(`/add?uid=${uid}&jwt=${encodeURIComponent(jwt)}`);
            const data = await res.json();

            if (data.success) {
                showAddResult('Friend request sent successfully!', 'success');
            } else {
                const raw = (data.raw_response || '').toLowerCase();
                let msg = '';

                if (raw.includes('br_friend_already_sent_request')) {
                    msg = `Friend request already sent to ${uid}.`;
                } else if (raw.includes('br_friend_not_same_region')) {
                    msg = `You cannot send a request to ${uid} because the region is different.`;
                } else if (raw.includes('br_friend_max_request')) {
                    msg = `The player's request list is full (maximum requests reached).`;
                } else if (raw.includes('br_friend_duplicate')) {
                    msg = `${uid} is already your friend.`;
                } else if (raw.includes('br_friend_self')) {
                    msg = `You cannot send a friend request to yourself.`;
                } else {
                    msg = `Failed to send request (${data.message || data.raw_response || 'unknown error'}).`;
                }

                showAddResult(msg, 'error');
            }
        }

        function showAddResult(message, type) {
            const el = document.getElementById('add-result');
            el.innerText = message;
            el.className = `mt-3 text-sm text-center font-medium px-3 py-2 rounded-lg ${
                type === 'success' ? 'bg-green-500/20 text-green-400 border border-green-500/40' : 'bg-red-500/20 text-red-400 border border-red-500/40'
            }`;
            el.style.display = 'block';
            clearTimeout(el._timeout);
            el._timeout = setTimeout(() => { el.style.display = 'none'; }, 3000);
        }

        async function removeFriend(uid) {
            const jwt = currentFriendsJwt;
            if (!jwt) {
                showTopNotification('Please load your friend list first.', 'error');
                return;
            }
            const friend = currentFriends.find(f => f.uid == uid);
            const name = friend?.name || uid;

            function createFallbackPopup() {
                const old = document.getElementById('popup-overlay-fallback');
                if (old) old.remove();

                const overlay = document.createElement('div');
                overlay.id = 'popup-overlay-fallback';
                overlay.className = 'popup-overlay';
                overlay.style.display = 'flex';
                overlay.innerHTML = `
                    <div class="popup-card">
                        <div class="text-4xl mb-3">🗑️</div>
                        <div class="text-xl font-bold text-white mb-2">Remove Friend</div>
                        <div class="text-gray-300 mb-6">Are you sure you want to remove <strong>${name}</strong> from your friend list?</div>
                        <div class="flex justify-center gap-4">
                            <button id="popup-cancel-fb" class="btn-outline">Cancel</button>
                            <button id="popup-confirm-fb" class="btn-glow">Yes, remove</button>
                        </div>
                    </div>`;
                document.body.appendChild(overlay);

                return new Promise((resolve) => {
                    document.getElementById('popup-confirm-fb').addEventListener('click', () => {
                        overlay.remove();
                        resolve(true);
                    });
                    document.getElementById('popup-cancel-fb').addEventListener('click', () => {
                        overlay.remove();
                        resolve(false);
                    });
                    overlay.addEventListener('click', (e) => {
                        if (e.target === overlay) {
                            overlay.remove();
                            resolve(false);
                        }
                    });
                });
            }

            let confirmed;
            try {
                if (document.getElementById('popup-overlay')) {
                    confirmed = await showPopup({
                        icon: '🗑️',
                        title: 'Remove Friend',
                        message: `Are you sure you want to remove <strong>${name}</strong> from your friend list?`,
                        confirmText: 'Yes, remove',
                        cancelText: 'Cancel'
                    });
                } else {
                    confirmed = await createFallbackPopup();
                }
            } catch (e) {
                confirmed = await createFallbackPopup();
            }

            if (!confirmed) return;

            const res = await fetch(`/remove?uid=${uid}&jwt=${encodeURIComponent(jwt)}`);
            const data = await res.json();
            if (data.success) {
                showTopNotification('Friend removed!', 'success');
                loadFriends();
            } else {
                showTopNotification('Error removing friend.', 'error');
            }
        }

        async function acceptRequest(uid) {
            const jwt = currentRequestsJwt;
            if (!jwt) {
                showTopNotification('Please load requests first.', 'error');
                return;
            }
            const request = currentRequests.find(r => r.uid == uid);
            const name = request?.nickname || uid;
            const confirmed = await showPopup({
                icon: '✅',
                title: 'Accept Friend Request',
                message: `Accept friend request from <strong>${name}</strong>?`,
                confirmText: 'Yes, accept',
                cancelText: 'Cancel'
            });
            if (!confirmed) return;
            const res = await fetch(`/accept?uid=${uid}&jwt=${encodeURIComponent(jwt)}`);
            const data = await res.json();
            if (data.success) {
                showTopNotification('Friend request accepted! 🎉', 'success');
                loadRequests();
            } else {
                showTopNotification('Error: ' + (data.error || ''), 'error');
            }
        }

        async function rejectRequest(uid) {
            const jwt = currentRequestsJwt;
            if (!jwt) {
                showTopNotification('Please load requests first.', 'error');
                return;
            }
            const request = currentRequests.find(r => r.uid == uid);
            const name = request?.nickname || uid;
            const confirmed = await showPopup({
                icon: '🗑️',
                title: 'Reject Friend Request',
                message: `Reject friend request from <strong>${name}</strong>?`,
                confirmText: 'Yes, reject',
                cancelText: 'Cancel'
            });
            if (!confirmed) return;
            const res = await fetch(`/reject?uid=${uid}&jwt=${encodeURIComponent(jwt)}`);
            const data = await res.json();
            if (data.success) {
                showTopNotification('Friend request rejected.', 'success');
                loadRequests();
            } else {
                showTopNotification('Error: ' + (data.error || ''), 'error');
            }
        }

        async function loadRequests() {
            const btn = document.querySelector('#requests-tab .btn-glow');
            const origText = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading…';
            btn.disabled = true;

            const type = document.getElementById('token-type-requests').value;
            let jwt = '';

            try {
                if (type === 'jwt') {
                    jwt = document.getElementById('jwt-requests').value.trim();
                } else if (type === 'access') {
                    const access = document.getElementById('access-requests').value.trim();
                    const openid = document.getElementById('openid-requests').value.trim();
                    if (!access) throw new Error('Please enter an Access Token.');
                    const res = await fetch(`/convert/access?access_token=${encodeURIComponent(access)}&open_id=${encodeURIComponent(openid)}`);
                    const data = await res.json();
                    if (data.success) jwt = data.jwt;
                    else throw new Error(data.error || 'Conversion failed');
                } else if (type === 'guest') {
                    const uid = document.getElementById('guest-uid-requests').value.trim();
                    const pass = document.getElementById('guest-pass-requests').value.trim();
                    if (!uid || !pass) throw new Error('Please enter UID and Password.');
                    const res = await fetch(`/convert/guest?uid=${uid}&password=${pass}`);
                    const data = await res.json();
                    if (data.success) jwt = data.jwt;
                    else throw new Error(data.error || 'Conversion failed');
                } else if (type === 'eat') {
                    const eat = document.getElementById('eat-requests').value.trim();
                    if (!eat) throw new Error('Please enter an EAT token or URL.');
                    const res = await fetch(`/convert/eat?eat_token=${encodeURIComponent(eat)}`);
                    const data = await res.json();
                    if (data.success) jwt = data.jwt;
                    else throw new Error(data.error || 'Conversion failed');
                }
            } catch (e) {
                btn.innerHTML = origText;
                btn.disabled = false;
                showTopNotification('Failed to obtain JWT: ' + e.message, 'error');
                return;
            }

            if (!jwt) {
                btn.innerHTML = origText;
                btn.disabled = false;
                showTopNotification('Please enter valid credentials.', 'error');
                return;
            }

            currentRequestsJwt = jwt;

            try {
                const res = await fetch(`/req-list?jwt=${encodeURIComponent(jwt)}`);
                const data = await res.json();
                btn.innerHTML = origText;
                btn.disabled = false;

                if (data.status === 'success') {
                    currentRequests = data.requests || [];
                    document.getElementById('requests-list-container').style.display = 'block';
                    renderRequests(currentRequests);

                    try {
                        const payload = JSON.parse(atob(currentRequestsJwt.split('.')[1]));
                        const rawNick = payload.nickname || 'Unknown';
                        document.getElementById('req-self-name').innerText = decodeFFName(rawNick);
                        document.getElementById('req-self-uid').innerText = payload.account_id || 'Unknown';
                        document.getElementById('req-self-region').innerText = payload.lock_region || 'Unknown';
                        document.getElementById('req-self-version').innerText = payload.release_version || 'Unknown';
                        document.getElementById('request-self-card').classList.remove('hidden');
                    } catch (e) {
                        document.getElementById('request-self-card').classList.add('hidden');
                    }

                    document.getElementById('total-requests-display').innerText =
                        `Total Requests: ${currentRequests.length}`;

                    const container = document.getElementById('requests-list-container');
                    const y = container.getBoundingClientRect().top + window.scrollY - 90;
                    window.scrollTo({ top: y, behavior: 'smooth' });
                } else {
                    showTopNotification('Failed to load requests: ' + (data.error_message || 'Unknown error'), 'error');
                }
            } catch (e) {
                btn.innerHTML = origText;
                btn.disabled = false;
                showTopNotification('Network error. Please try again.', 'error');
            }
        }

        function renderRequests(requests) {
            const grid = document.getElementById('requests-grid');
            if (requests.length === 0) {
                grid.innerHTML = `
                    <div class="col-span-full glass-card p-8 text-center text-gray-400">
                        <i class="fas fa-inbox text-4xl mb-3"></i>
                        <p class="text-lg font-semibold">No pending friend requests</p>
                        <p class="text-sm mt-1">When someone sends you a request, it will appear here.</p>
                    </div>`;
                return;
            }

            let html = '';
            requests.forEach(r => {
                html += `
                    <div class="glass-card p-4 flex flex-col">
                        <div>
                            <h3 class="font-bold text-purple-300">${r.nickname}</h3>
                            <p class="text-sm text-gray-400">UID: ${r.uid}</p>
                            <p class="text-sm text-gray-400">Level: ${r.level}</p>
                        </div>
                        <div class="flex gap-2 mt-4 justify-end">
                            <button onclick="acceptRequest('${r.uid}')" class="btn-accept"><i class="fas fa-check"></i> Accept</button>
                            <button onclick="rejectRequest('${r.uid}')" class="btn-reject"><i class="fas fa-times"></i> Reject</button>
                        </div>
                    </div>
                `;
            });
            grid.innerHTML = html;
        }

        function filterRequests() {
            const query = document.getElementById('request-search').value.toLowerCase();
            const filtered = currentRequests.filter(r => 
                r.uid.toString().includes(query) || r.nickname.toLowerCase().includes(query)
            );
            renderRequests(filtered);
        }

        document.addEventListener('DOMContentLoaded', () => {
            AOS.init({ once: true, duration: 1000, offset: 50 });
            VANTA.WAVES({
                el: "#vanta-bg",
                mouseControls: true, touchControls: true, gyroControls: false,
                minHeight: 200.00, minWidth: 200.00, scale: 1.00, scaleMobile: 1.00,
                color: 0x20023, shininess: 25.00, waveHeight: 15.00, waveSpeed: 0.75, zoom: 0.85
            });
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🔥 Free Fire Friends Manager running on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
