import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
import json
import warnings
import os
import subprocess
import traceback

# Prevent OpenMP runtime conflict crashes
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"

# Force UTF-8 encoding for Python subprocesses
os.environ["PYTHONIOENCODING"] = "utf-8"

# Get the directory where this script/engine lives (the tools folder)
IS_FROZEN = getattr(sys, 'frozen', False)
if IS_FROZEN:
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    candidate = EXE_DIR
    FOUND_TOOLS_DIR = None
    for _ in range(4):
        if (os.path.exists(os.path.join(candidate, "models")) or 
            os.path.exists(os.path.join(candidate, "win", "ffmpeg.exe")) or 
            os.path.exists(os.path.join(candidate, "mac", "ffmpeg")) or
            os.path.exists(os.path.join(candidate, "mac", "ffmpeg_arm64"))):
            FOUND_TOOLS_DIR = candidate
            break
        candidate = os.path.dirname(candidate)
    SCRIPT_DIR = FOUND_TOOLS_DIR if FOUND_TOOLS_DIR else os.path.dirname(EXE_DIR)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    SCRIPT_DIR = EXE_DIR

# Add the local tools folder to PATH so whisper can find the local ffmpeg.exe
if sys.platform == "darwin":
    mac_bin_dir = os.path.join(SCRIPT_DIR, "mac")
    if mac_bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + mac_bin_dir
else:
    win_bin_dir = os.path.join(SCRIPT_DIR, "win")
    if win_bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + win_bin_dir
    # Check potential CUDA DLL locations (embedded cuda folder, win/dlls, or pip nvidia packages)
    cuda_dirs = [
        os.path.join(EXE_DIR, "cuda"),
        os.path.join(SCRIPT_DIR, "win", "engine", "cuda"),
        os.path.join(SCRIPT_DIR, "win", "cuda"),
        os.path.join(SCRIPT_DIR, "win", "dlls"),
    ]
    if not IS_FROZEN:
        try:
            sp = os.path.join(sys.prefix, "Lib", "site-packages")
            cuda_dirs.append(os.path.join(sp, "nvidia", "cublas", "bin"))
            cuda_dirs.append(os.path.join(sp, "nvidia", "cudnn", "bin"))
        except Exception:
            pass

    for cdir in cuda_dirs:
        if os.path.isdir(cdir):
            if cdir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = cdir + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(cdir)
                except Exception:
                    pass
warnings.filterwarnings("ignore")

def is_cuda_usable():
    """Verify if NVIDIA CUDA GPU acceleration runtime (e.g. cuBLAS DLLs) is truly present and loadable."""
    if sys.platform != "win32":
        return False
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return False
    except Exception:
        return False

    import ctypes
    # Check if cuBLAS 12 or 11 can actually be loaded
    for dll_name in ["cublas64_12.dll", "cublas64_11.dll"]:
        try:
            ctypes.CDLL(dll_name)
            return True
        except Exception:
            pass
    return False

def get_ssl_context():
    """Returns a robust SSL context using certifi or OS root certificates across Windows and macOS."""
    import ssl
    try:
        import certifi
        cafile = certifi.where()
        if os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
    except Exception:
        pass
    for ca_path in [
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/certs/ca-certificates.crt"
    ]:
        if os.path.exists(ca_path):
            try:
                return ssl.create_default_context(cafile=ca_path)
            except Exception:
                pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()

PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEFXqgmcQCRgBFT0fyOYPlztPAYvpU
cXpHytneusbAk74qfrmdBIA8UZXb2bKO1yiE7ce5kF2UJk9ErPqzbRHYxQ==
-----END PUBLIC KEY-----"""

def get_system_machine_guid():
    import platform
    try:
        if platform.system() == 'Windows':
            reg_out = subprocess.check_output(['reg', 'query', 'HKLM\\SOFTWARE\\Microsoft\\Cryptography', '/v', 'MachineGuid'], stderr=subprocess.DEVNULL)
            import re
            match = re.search(r'MachineGuid\s+REG_SZ\s+([a-zA-Z0-9-]+)', reg_out.decode(), re.IGNORECASE)
            return "".join(c for c in match.group(1) if c.isalnum()) if match else ""
        else:
            out = subprocess.check_output("ioreg -rd1 -c IOPlatformExpertDevice | awk '/IOPlatformUUID/ { split($0, line, \"\\\"\"); printf(\"%s\\n\", line[4]); }'", shell=True, stderr=subprocess.DEVNULL)
            return "".join(c for c in out.decode().strip() if c.isalnum())
    except Exception:
        return ""

def unseal_device_identity():
    import platform
    import hashlib
    try:
        if platform.system() == 'Darwin':
            cache_dir = os.path.expanduser('~/Library/Application Support/QuickSubPro')
        else:
            cache_dir = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'QuickSubPro')
        file_path = os.path.join(cache_dir, '.device_identity')
        if not os.path.exists(file_path):
            return ""
        guid = get_system_machine_guid()
        if not guid:
            return ""
        with open(file_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if not raw.get('iv') or not raw.get('tag') or not raw.get('data'):
            return ""
        key = hashlib.pbkdf2_hmac('sha256', guid.encode('utf-8'), b'QS_CACHE_SALT_#8492_PRO', 2000, 32)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        ciphertext = bytes.fromhex(raw['data']) + bytes.fromhex(raw['tag'])
        iv = bytes.fromhex(raw['iv'])
        decrypted = aesgcm.decrypt(iv, ciphertext, None)
        return decrypted.decode('utf-8').strip()
    except Exception:
        return ""

def get_hardware_id():
    import platform
    try:
        if platform.system() == 'Windows':
            out = subprocess.check_output(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_BaseBoard).SerialNumber'], stderr=subprocess.DEVNULL)
            hwid = "".join(c for c in out.decode().strip() if c.isalnum())
            is_zeroes = all(c == '0' for c in hwid) if hwid else False
            is_placeholder = any(hwid.lower().startswith(p) for p in ['none', 'default', 'tobedefined', 'tobefilled', 'baseboard', 'unknown']) if hwid else False
            is_generic = not hwid or len(hwid) < 3 or is_zeroes or is_placeholder
            if is_generic:
                guid = get_system_machine_guid()
                if guid and len(guid) >= 4:
                    hwid = guid
            if not hwid or len(hwid) < 4:
                unsealed = unseal_device_identity()
                if unsealed and len(unsealed) >= 4:
                    hwid = unsealed
                else:
                    hwid = 'WIN_' + os.environ.get('COMPUTERNAME', 'DEVICE')
            return hwid
        else:
            out = subprocess.check_output("ioreg -rd1 -c IOPlatformExpertDevice | awk '/IOPlatformUUID/ { split($0, line, \"\\\"\"); printf(\"%s\\n\", line[4]); }'", shell=True, stderr=subprocess.DEVNULL)
            hwid = "".join(c for c in out.decode().strip() if c.isalnum())
            if not hwid or len(hwid) < 4:
                unsealed = unseal_device_identity()
                if unsealed and len(unsealed) >= 4:
                    hwid = unsealed
                else:
                    hwid = 'MAC_' + os.environ.get('USER', 'DEVICE')
            return hwid
    except Exception:
        return 'WIN_' + os.environ.get('COMPUTERNAME', 'DEVICE') if platform.system() == 'Windows' else 'MAC_' + os.environ.get('USER', 'DEVICE')

def load_license_cache(hwid):
    import platform
    import hashlib
    if platform.system() == 'Darwin':
        cache_file = os.path.expanduser('~/Library/Application Support/QuickSubPro/license_cache.json')
    else:
        cache_file = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'QuickSubPro', 'license_cache.json')
        
    if not os.path.exists(cache_file):
        return None
        
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            raw = f.read().strip()
            
        # Case A: Modern Authenticated AES-256-GCM Envelope
        if raw.startswith('{'):
            try:
                parsed_env = json.loads(raw)
                if 'iv' in parsed_env and 'tag' in parsed_env and 'data' in parsed_env:
                    key = hashlib.pbkdf2_hmac('sha256', hwid.encode('utf-8'), b'QS_CACHE_SALT_#8492_PRO', 10000, 32)
                    try:
                        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                        aesgcm = AESGCM(key)
                        ciphertext = bytes.fromhex(parsed_env['data']) + bytes.fromhex(parsed_env['tag'])
                        iv = bytes.fromhex(parsed_env['iv'])
                        decrypted = aesgcm.decrypt(iv, ciphertext, None)
                        return json.loads(decrypted.decode('utf-8'))
                    except Exception:
                        pass
            except Exception:
                pass

        # Case B: Legacy XOR format (one-time migration support)
        text = ""
        for i in range(0, len(raw), 2):
            char_code = int(raw[i:i+2], 16) ^ ord(hwid[(i//2) % len(hwid)])
            text += chr(char_code)
            
        return json.loads(text)
    except Exception:
        return None

def get_os_monotonic_watermark():
    import platform
    if platform.system() == 'Windows':
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\QuickSubPro")
            val, _ = winreg.QueryValueEx(key, "LastActiveWatermark")
            winreg.CloseKey(key)
            return int(val)
        except Exception:
            return 0
    else:
        try:
            wm_path = os.path.expanduser('~/.quicksub_wm')
            if os.path.exists(wm_path):
                with open(wm_path, 'r', encoding='utf-8') as f:
                    return int(f.read().strip())
        except Exception:
            return 0
    return 0

def verify_execution_session(config):
    import time
    import hmac
    import hashlib

    handshake = config.get("security_handshake")
    if not handshake or not isinstance(handshake, dict):
        return {"valid": False, "error": "Please activate a valid QuickSub Pro license to generate captions."}

    token = handshake.get("token")
    timestamp = handshake.get("timestamp")
    bucket = handshake.get("bucket")

    if not token or not timestamp:
        return {"valid": False, "error": "Please activate a valid QuickSub Pro license to generate captions."}

    # Verify timestamp freshness (within 15 minutes window)
    now_ms = time.time() * 1000
    if abs(now_ms - timestamp) > 15 * 60 * 1000:
        return {"valid": False, "error": "Session timed out. Please click Generate Captions again."}

    hwid = get_hardware_id()
    cache = load_license_cache(hwid)
    if not cache:
        return {"valid": False, "error": "Please activate a valid QuickSub Pro license."}

    # Strict ECDSA P-256 signature verification (ensuring genuine Supabase signed lease)
    signed_payload_str = cache.get("signed_payload")
    signature_hex = cache.get("signature")
    if not signed_payload_str or not signature_hex:
        return {"valid": False, "error": "License verification failed: unauthenticated or corrupted session."}

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

        pub_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode('utf-8'))
        sig_bytes = bytes.fromhex(signature_hex)
        if len(sig_bytes) != 64:
            return {"valid": False, "error": "License verification failed: invalid signature format."}
        r = int.from_bytes(sig_bytes[:32], 'big')
        s = int.from_bytes(sig_bytes[32:], 'big')
        der_sig = encode_dss_signature(r, s)
        pub_key.verify(der_sig, signed_payload_str.encode('utf-8'), ec.ECDSA(hashes.SHA256()))

        payload = json.loads(signed_payload_str)
        if payload.get("product") != "QuickSub Pro":
            return {"valid": False, "error": "This license is not valid for QuickSub Pro."}
        if payload.get("hwid") != hwid:
            return {"valid": False, "error": "This license is already active on another device."}
    except Exception:
        return {"valid": False, "error": "License verification failed: cryptographic signature untrusted or tampered."}

    # Strict cryptographic seed verification (eliminating legacy unverified bypass)
    seed = cache.get("entanglement_seed")
    if not seed:
        return {"valid": False, "error": "License session corrupted. Please reactivate your license in settings."}

    # Validate against current, adjacent, and declared 15-minute sliding window buckets
    current_bucket = int(time.time() // (60 * 15))
    valid = False
    candidates = [current_bucket, current_bucket - 1, current_bucket + 1]
    if bucket is not None and bucket not in candidates:
        candidates.append(bucket)

    for b in candidates:
        data = f"{hwid}:{b}:QuickSub Pro".encode('utf-8')
        expected_token = hmac.new(seed.encode('utf-8'), data, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_token, token):
            valid = True
            break

    if not valid:
        return {"valid": False, "error": "License verification failed. Please reactivate your license in settings."}

    # Anti-Clock Rollback check in Python (Dual-Layer Monotonic Watermark):
    cache_date = cache.get("cache_date")
    last_active = cache.get("last_active_timestamp") or cache_date
    os_watermark = get_os_monotonic_watermark()
    highest_watermark = max(last_active or 0, os_watermark or 0)

    if cache_date and (cache_date > now_ms + 180000 or now_ms - cache_date < -180000):
        return {"valid": False, "error": "System clock anomaly detected. Please ensure your clock is set correctly."}
    if highest_watermark > 0 and (highest_watermark - now_ms > 60000):
        return {"valid": False, "error": "System clock anomaly detected. Please ensure your clock is set correctly."}

    # Strict Expiration Enforcement (Zero Grace for 7-Day Free Trials):
    license_key = str(cache.get("gumroad_key") or "")
    signed_is_trial = bool(payload.get("is_trial") or str(payload.get("license_key") or "").startswith("TRIAL-"))
    cache_is_trial = bool(cache.get("is_trial") or license_key.startswith("TRIAL-"))
    is_trial = signed_is_trial or cache_is_trial

    # Cryptographically verified expiration is primary source of truth:
    exp_str = payload.get("exp") or cache.get("expiration_date")

    if is_trial and not exp_str:
        return {"valid": False, "error": "Invalid trial expiration data. Please reactivate your trial in settings."}

    if exp_str:
        import datetime
        try:
            cleaned_exp = exp_str.replace("Z", "+00:00") if str(exp_str).endswith("Z") else str(exp_str)
            if "T" in cleaned_exp:
                exp_dt = datetime.datetime.fromisoformat(cleaned_exp)
            else:
                exp_dt = datetime.datetime.strptime(cleaned_exp.split()[0], "%Y-%m-%d")
                exp_dt = exp_dt.replace(hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc)

            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)

            now_dt = datetime.datetime.now(datetime.timezone.utc)
            grace_seconds = 0 if is_trial else (48 * 3600)

            if (now_dt.timestamp() - exp_dt.timestamp()) > grace_seconds:
                if is_trial:
                    return {"valid": False, "error": "Your 7-day QuickSub Pro trial has expired. Please activate a full license."}
                else:
                    return {"valid": False, "error": "Your QuickSub Pro license lease has expired. Please renew in settings."}
        except Exception:
            if is_trial:
                return {"valid": False, "error": "Corrupted trial expiration timestamp. Please reactivate your trial."}

    return {"valid": True}




import shutil
try:
    import tqdm
    import tqdm.auto
    class DownloadProgressTqdm(tqdm.auto.tqdm):
        _last_printed_pct = -1

        def update(self, n=1):
            super().update(n)
            if self.total and self.total > 0:
                pct = min(100, max(0, int(self.n * 100 / self.total)))
                if pct != DownloadProgressTqdm._last_printed_pct:
                    DownloadProgressTqdm._last_printed_pct = pct
                    print(f"[MODEL_DOWNLOAD_PROGRESS: {pct}]", flush=True)
                    print(f"[STATUS: Downloading AI Model {pct}%]", flush=True)
except Exception:
    class DownloadProgressTqdm:
        _last_printed_pct = -1
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get('total', 0) or 0
            self.n = kwargs.get('initial', 0) or 0
        def update(self, n=1):
            self.n += n
            if self.total > 0:
                pct = min(100, max(0, int(self.n * 100 / self.total)))
                if pct != DownloadProgressTqdm._last_printed_pct:
                    DownloadProgressTqdm._last_printed_pct = pct
                    print(f"[MODEL_DOWNLOAD_PROGRESS: {pct}]", flush=True)
                    print(f"[STATUS: Downloading AI Model {pct}%]", flush=True)
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

def is_model_complete(models_dir):
    """Checks if the faster-whisper large-v3-turbo model is fully downloaded without corrupted or partial files."""
    if not os.path.isdir(models_dir):
        return False
    # Check for any lingering .incomplete files
    for root, _, files in os.walk(models_dir):
        if any(f.endswith('.incomplete') or f.endswith('.tmp') for f in files):
            return False
    has_bin = False
    has_config = False
    for root, _, files in os.walk(models_dir):
        for f in files:
            if f == "model.bin":
                try:
                    if os.path.getsize(os.path.join(root, f)) >= 1400 * 1024 * 1024:
                        has_bin = True
                except Exception:
                    pass
            elif f == "config.json":
                has_config = True
    return has_bin and has_config

def cleanup_partial_models(models_dir):
    """Safely cleans up any temporary, incomplete, or corrupted model files."""
    try:
        if not os.path.isdir(models_dir):
            return
        # 1. Remove all .incomplete files
        for root, dirs, files in os.walk(models_dir, topdown=False):
            for f in files:
                if f.endswith(".incomplete") or f.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass
        # 2. Check if model is complete. If not, delete incomplete repo folder & locks
        if not is_model_complete(models_dir):
            repo_folder = os.path.join(models_dir, "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
            if os.path.isdir(repo_folder):
                try:
                    shutil.rmtree(repo_folder, ignore_errors=True)
                except Exception:
                    pass
            locks_folder = os.path.join(models_dir, ".locks")
            if os.path.isdir(locks_folder):
                try:
                    shutil.rmtree(locks_folder, ignore_errors=True)
                except Exception:
                    pass
            cachedir_tag = os.path.join(models_dir, "CACHEDIR.TAG")
            if os.path.exists(cachedir_tag):
                try:
                    os.remove(cachedir_tag)
                except Exception:
                    pass
    except Exception:
        pass

_active_download_dir = None

def _on_process_exit():
    global _active_download_dir
    if _active_download_dir:
        cleanup_partial_models(_active_download_dir)

import atexit
atexit.register(_on_process_exit)

def _term_signal_handler(signum, frame):
    _on_process_exit()
    sys.exit(1)

import signal
try:
    signal.signal(signal.SIGINT, _term_signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _term_signal_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _term_signal_handler)
except Exception:
    pass

def ensure_ai_model(models_dir):
    """
    Verifies that the faster-whisper model is present.
    If missing or incomplete, downloads with real 0-100% progress output.
    Cleans up partial files on any error or network interruption.
    """
    global _active_download_dir
    if is_model_complete(models_dir):
        return True, None

    _active_download_dir = models_dir
    print("[STATUS: Downloading AI Model 0%]", flush=True)
    print("[MODEL_DOWNLOAD_PROGRESS: 0]", flush=True)
    DownloadProgressTqdm._last_printed_pct = -1

    try:
        import huggingface_hub
        allow_patterns = [
            "config.json",
            "preprocessor_config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*"
        ]
        huggingface_hub.snapshot_download(
            "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            cache_dir=models_dir,
            allow_patterns=allow_patterns,
            tqdm_class=DownloadProgressTqdm
        )
        if not is_model_complete(models_dir):
            cleanup_partial_models(models_dir)
            _active_download_dir = None
            return False, "Download verification failed: model files incomplete."
            
        print("[MODEL_DOWNLOAD_PROGRESS: 100]", flush=True)
        _active_download_dir = None
        return True, None
    except Exception as dl_err:
        cleanup_partial_models(models_dir)
        _active_download_dir = None
        print(f"[DOWNLOAD_FAILED: NETWORK_LOST] Error: {dl_err}", flush=True)
        return False, "Download failed: Network lost. Connect to internet to download again."

try:
    from faster_whisper import WhisperModel
except ImportError:
    print(json.dumps({"error": "AI components not found. Please verify QuickSub Pro installation."}))
    sys.exit(1)

def extract_audio(input_file, start_time, duration, output_wav):
    import sys
    if sys.platform == "darwin":
        import platform
        is_arm = platform.machine().lower() in ["arm64", "aarch64"]
        arm_ffmpeg = os.path.join(SCRIPT_DIR, "mac", "ffmpeg_arm64")
        if is_arm and os.path.exists(arm_ffmpeg):
            ffmpeg_exe = arm_ffmpeg
        else:
            ffmpeg_exe = os.path.join(SCRIPT_DIR, "mac", "ffmpeg")
            
        if not os.path.exists(ffmpeg_exe):
            for sys_path in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
                if os.path.exists(sys_path):
                    ffmpeg_exe = sys_path
                    break

        if os.path.exists(ffmpeg_exe) and not os.access(ffmpeg_exe, os.X_OK):
            try:
                os.chmod(ffmpeg_exe, 0o755)
            except Exception:
                pass
    else:
        ffmpeg_exe = os.path.join(SCRIPT_DIR, "win", "ffmpeg.exe")
    cmd = [
        ffmpeg_exe, "-y",
        "-i", input_file,
        "-ss", str(start_time),
        "-t", str(duration),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        "-af", "highpass=f=60,lowpass=f=7500",
        output_wav
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        # Automatic safety fallback without audio filter in case ffmpeg version or format rejects filter
        cmd_fallback = [
            ffmpeg_exe, "-y",
            "-i", input_file,
            "-ss", str(start_time),
            "-t", str(duration),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            output_wav
        ]
        try:
            subprocess.run(cmd_fallback, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            return True
        except Exception as e:
            print(f"FFmpeg extraction fallback failed: {str(e)}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Extraction error: {str(e)}", file=sys.stderr)
        return False

def get_dynamic_silences(wav_path, min_duration=0.3):
    import wave, struct, math
    try:
        import numpy as np
        with wave.open(wav_path, 'rb') as wf:
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            data = wf.readframes(n_frames)
        
        samples = np.frombuffer(data, dtype=np.int16)
        
        window_size = int(sample_rate * 0.05)
        num_windows = len(samples) // window_size
        if num_windows == 0: return []
        
        truncated_samples = samples[:num_windows * window_size]
        reshaped = truncated_samples.reshape(-1, window_size)
        
        rms_values = np.sqrt(np.mean(reshaped.astype(np.float32)**2, axis=1)).tolist()
            
        if not rms_values: return []
        
        sorted_rms = sorted(rms_values)
        noise_floor = sorted_rms[int(len(sorted_rms) * 0.1)]
        peak_rms = sorted_rms[int(len(sorted_rms) * 0.95)]
        
        if peak_rms < 100: return []
        
        threshold = noise_floor + (peak_rms - noise_floor) * 0.1
        
        silences = []
        in_silence = False
        silence_start_idx = 0
        
        for i, rms in enumerate(rms_values):
            is_quiet = rms < threshold
            if is_quiet and not in_silence:
                in_silence = True
                silence_start_idx = i
            elif not is_quiet and in_silence:
                in_silence = False
                duration = (i - silence_start_idx) * 0.05
                if duration >= min_duration:
                    silences.append((silence_start_idx * 0.05, i * 0.05))
                    
        if in_silence:
            duration = (len(rms_values) - silence_start_idx) * 0.05
            if duration >= min_duration:
                silences.append((silence_start_idx * 0.05, len(rms_values) * 0.05))
                
        return silences
    except Exception as e:
        return []

def generate_captions(config):
    temp_wav = None
    try:
        audio_file = config.get("audio_file")
        start_time = float(config.get("start_time", 0.0))
        duration = float(config.get("duration", 0.0))
        language = config.get("language", "auto")
        words_per_caption = int(config.get("words_per_caption", 1))
        prompt = config.get("prompt", "")
        task = config.get("task", "transcribe")
        transliterate_to_english = config.get("transliterate_to_english", False)
        fill_silence_gaps = config.get("fill_silence_gaps", False)
        max_gap_seconds = float(config.get("max_gap_seconds", 0.5))
        
        # 1. Extract trimmed audio
        print("[STATUS: Extracting Audio...]", flush=True)
        print("[PROGRESS: 10]", flush=True)
        import tempfile, uuid
        
        # Make temp directory path unicode-safe for Windows FFmpeg
        temp_dir = tempfile.gettempdir()
        if sys.platform == "win32":
            try:
                import ctypes
                buffer = ctypes.create_unicode_buffer(500)
                if ctypes.windll.kernel32.GetShortPathNameW(temp_dir, buffer, 500) > 0:
                    temp_dir = buffer.value
            except Exception:
                pass

        temp_wav = os.path.join(temp_dir, f"quicksub_extract_{uuid.uuid4().hex[:8]}.wav")
        
        # Give Whisper 0.2s of breathing room to avoid dropping the first word
        pad = 0.2 if start_time >= 0.2 else start_time
        extract_start = start_time - pad
        extract_duration = duration + pad
        
        success = extract_audio(audio_file, extract_start, extract_duration, temp_wav)
        if not success:
            return {"error": "Failed to extract audio. Please check your media format."}
            
        # 2. Transcribe
        print("[PROGRESS: 20]", flush=True)
        models_dir = os.path.join(SCRIPT_DIR, "models")
        
        # Ensure model is fully downloaded and valid with live progress and network safety
        model_ok, dl_err = ensure_ai_model(models_dir)
        if not model_ok:
            return {"error": dl_err or "AI model missing. Connect to internet to download."}

        print("[STATUS: Loading AI Model...]", flush=True)
        print("[PROGRESS: 30]", flush=True)
        
        model_name = "large-v3-turbo"
        
        # Performance optimization with multi-tier resilience:
        # 1. macOS: Use int8 CPU mode (AVX2/NEON vector optimized), fallback to default float32 if needed.
        # 2. Windows: Try CUDA float16 GPU mode ONLY if CUDA runtime DLLs are usable, fallback to CPU int8, and ultimate fallback to default float32.
        model = None
        device_used = "cpu"
        cpu_threads = max(1, min(4, (os.cpu_count() or 4)))
        try:
            if sys.platform == "darwin":
                try:
                    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads, download_root=models_dir)
                    device_used = "cpu (int8)"
                except Exception:
                    model = WhisperModel(model_name, device="cpu", compute_type="default", cpu_threads=cpu_threads, download_root=models_dir)
                    device_used = "cpu (default)"
            else:
                cuda_available = is_cuda_usable()
                if cuda_available:
                    try:
                        # Attempt GPU acceleration with float16 first
                        model = WhisperModel(model_name, device="cuda", compute_type="float16", download_root=models_dir)
                        device_used = "cuda (float16)"
                    except Exception as cuda_err:
                        print(f"[Device Notice] CUDA initialization failed ({cuda_err}), falling back to CPU mode", file=sys.stderr, flush=True)
                        cuda_available = False
                
                if not cuda_available:
                    try:
                        # Fallback to high-performance CPU int8 mode
                        model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads, download_root=models_dir)
                        device_used = "cpu (int8)"
                    except Exception as cpu_err:
                        # Ultimate safety fallback to CPU default
                        print(f"[Device Notice] CPU int8 failed ({cpu_err}), falling back to CPU default mode", file=sys.stderr, flush=True)
                        model = WhisperModel(model_name, device="cpu", compute_type="default", cpu_threads=cpu_threads, download_root=models_dir)
                        device_used = "cpu (default)"
        except Exception as model_err:
            err_str = str(model_err).lower()
            cleanup_partial_models(models_dir)
            if any(k in err_str for k in ["huggingface", "connect", "network", "offline", "resolve", "http", "socket"]):
                return {"error": "Download failed: Network lost. Connect to internet to download again."}
            raise model_err

        print(f"[Device Notice] Active compute device: {device_used}", file=sys.stderr, flush=True)
        print("[STATUS: Transcribing...]", flush=True)
        print("[PROGRESS: 40]", flush=True)
        
        transcribe_args = {"word_timestamps": True, "task": task, "condition_on_previous_text": False}
        if language != "auto":
            transcribe_args["language"] = language
        if prompt:
            transcribe_args["initial_prompt"] = prompt
            
        # Selective Beam Search for Hindi (explores top 5 candidate sequence hypotheses for optimal grammar)
        # BUG FIX: Beam search for Hindi often causes empty segments ("no transcribe zones"). 
        # Commented out to force Greedy Decoding instead, which prevents missing audio zones.
        # if language == "hi":
        #     transcribe_args["beam_size"] = 5
        #     transcribe_args["best_of"] = 5
        #     transcribe_args["patience"] = 1.0
        #     transcribe_args["length_penalty"] = 1.0
            
        # Strict greedy decoding prevents sampling hallucinations and phoneme scrambling
        transcribe_args["temperature"] = 0.0
        
        # Drop silence/noise that causes hallucinations
        transcribe_args["no_speech_threshold"] = 0.6 
        
        # VAD filter: speech detection
        transcribe_args["vad_filter"] = True
        transcribe_args["vad_parameters"] = dict(min_silence_duration_ms=300, speech_pad_ms=400, threshold=0.35)
        
        # Repetition penalty and 3-gram blocking prevent repetition loops
        transcribe_args["repetition_penalty"] = 1.15
        transcribe_args["no_repeat_ngram_size"] = 3
        
        # Reset compression ratio to 2.4
        transcribe_args["compression_ratio_threshold"] = 2.4
        
        # Hallucination silence threshold suppresses hallucinated speech during silence/music pauses
        transcribe_args["hallucination_silence_threshold"] = 1.5
        
        def _execute_transcription(active_model):
            seg_gen, inf = active_model.transcribe(temp_wav, **transcribe_args)
            collected_segments = []
            for segment in seg_gen:
                collected_segments.append(segment)
                # Safely calculate progress between 40% and 80%
                if duration > 0:
                    progress_fraction = min(1.0, segment.end / duration)
                    current_pct = 40 + int(progress_fraction * 40)
                    print(f"[PROGRESS: {current_pct}]", flush=True)
            return collected_segments, inf

        try:
            segments, info = _execute_transcription(model)
        except Exception as transcribe_err:
            err_msg = str(transcribe_err).lower()
            if "cuda" in device_used and any(k in err_msg for k in ["cuda", "cublas", "cudnn", "out of memory", "not found", "cannot be loaded", "driver"]):
                print(f"[Device Notice] CUDA transcription failed ({transcribe_err}), seamlessly switching to CPU fallback...", file=sys.stderr, flush=True)
                print("[STATUS: Switching to CPU...]", flush=True)
                try:
                    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads, download_root=models_dir)
                    device_used = "cpu (int8)"
                except Exception:
                    model = WhisperModel(model_name, device="cpu", compute_type="default", cpu_threads=cpu_threads, download_root=models_dir)
                    device_used = "cpu (default)"
                print(f"[Device Notice] Active compute device: {device_used}", file=sys.stderr, flush=True)
                segments, info = _execute_transcription(model)
            else:
                raise transcribe_err
        
        # Post-process: clean foreign script leaks from Hindi and Marathi transcription
        detected_lang = info.language if info and hasattr(info, 'language') else language
        if detected_lang in ["hi", "mr"]:
            import re, unicodedata
            def clean_hindi_segment(text):
                """Normalize Unicode (NFC) and remove foreign script characters (Arabic, Greek, Hebrew, Armenian, etc.) 
                that leak into Hindi output while preserving full English words, numbers, and punctuation."""
                text = unicodedata.normalize('NFC', text)
                foreign_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\u0370-\u03FF\u0590-\u05FF\u0530-\u058F\u0100-\u024F]')
                devanagari_pattern = re.compile(r'[\u0900-\u097F\u1CD0-\u1CFF\uA8E0-\uA8FF]')
                
                tokens = text.split()
                cleaned_tokens = []
                for token in tokens:
                    has_foreign = bool(foreign_pattern.search(token))
                    has_devanagari = bool(devanagari_pattern.search(token))
                    
                    if has_foreign and has_devanagari:
                        cleaned = foreign_pattern.sub('', token)
                        if cleaned.strip():
                            cleaned_tokens.append(cleaned)
                    elif has_foreign and not has_devanagari:
                        continue
                    else:
                        if has_devanagari:
                            latin_chars = re.findall(r'[a-zA-Z]', token)
                            if latin_chars:
                                cleaned = re.sub(r'[a-zA-Z]', '', token)
                                if cleaned.strip():
                                    cleaned_tokens.append(cleaned)
                                continue
                        cleaned_tokens.append(token)
                
                return ' '.join(cleaned_tokens)
            
            # Apply cleaning to each segment's text
            for seg_idx in range(len(segments)):
                seg = segments[seg_idx]
                original_text = seg.text
                cleaned_text = clean_hindi_segment(original_text)
                if cleaned_text != original_text:
                    # Create a modified segment with cleaned text
                    seg._text = cleaned_text if hasattr(seg, '_text') else None
                    # Use a wrapper approach since Segment objects may be read-only
                    class CleanedSegment:
                        def __init__(self, original, new_text):
                            self.start = original.start
                            self.end = original.end
                            self.text = new_text
                            self.words = getattr(original, 'words', None)
                        def __getattr__(self, name):
                            return getattr(self._original, name)
                    cs = CleanedSegment(seg, cleaned_text)
                    cs._original = seg
                    segments[seg_idx] = cs

        # DEBUG: Dump raw whisper output
        try:
            debug_path = os.path.join(tempfile.gettempdir(), "quicksub_whisper_raw.json")
            with open(debug_path, "w", encoding="utf-8") as df:
                serializable_segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
                json.dump({"segments": serializable_segments, "language": info.language}, df, ensure_ascii=False, indent=2)
        except Exception as e:
            print("Debug dump failed:", e)
        # Pre-clamp words and segment boundaries so Whisper attention smearing doesn't contaminate AI Grammar Corrector
        for seg in segments:
            if getattr(seg, 'words', None) and len(seg.words) > 0:
                for w in seg.words:
                    clean_len = len(str(getattr(w, 'word', '')).strip())
                    max_w_dur = 0.9 if clean_len <= 4 else 1.25
                    if (w.end - w.start) > max_w_dur:
                        w.end = max(round(w.start + max_w_dur, 2), w.start + 0.1)
                seg.end = seg.words[-1].end

        # AI Grammar Corrector
        ai_grammar_model = config.get("aiGrammarModel", "Off")
        gemini_api_key = config.get("geminiApiKey", "")
        ai_custom_prompt = config.get("aiCustomPrompt", "").strip()
        gemini_handled_hinglish = False
        
        if ai_grammar_model != "Off" and gemini_api_key:
            # Fallback cascade
            models_to_try = [ai_grammar_model]
            if ai_grammar_model not in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
                models_to_try.append("gemini-2.5-flash")
                
            print("[STATUS: Grammar Correcting...]", flush=True)
            import urllib.request, urllib.error, time
            
            batch_size = 50
            success_ai = False
            is_hinglish_mode = transliterate_to_english or (str(language).lower() == "hinglish")
            
            for attempt_model in models_to_try:
                try:
                    for i in range(0, len(segments), batch_size):
                        if i > 0:
                            time.sleep(0.5) # Gentle rate-limit pacing for long videos
                        batch = segments[i:i+batch_size]
                        prompt_text = ""
                        for idx, seg in enumerate(batch):
                            prompt_text += f"[{i+idx}] {seg.text}\n"
                            
                        if ai_custom_prompt:
                            system_instruction = ai_custom_prompt.replace("{detected_lang}", detected_lang)
                        elif is_hinglish_mode:
                            system_instruction = (
                                "You are an expert Hinglish caption assistant. "
                                "Convert any Devanagari/Hindi script into natural Roman-script Hinglish (e.g. 'नमस्ते' -> 'Namaste', 'आप कैसे हैं' -> 'Aap kaise hain'). "
                                "Fix grammar, punctuation, spelling, and remove filler words (um, uh, etc.). "
                                "Keep the text in clean, modern Roman alphabet Hinglish."
                            )
                        else:
                            system_instruction = (
                                f"You are an expert caption assistant. The original audio is in {detected_lang}. "
                                "Fix grammar, punctuation, spelling, and remove filler words (um, uh). "
                                "DO NOT add conversational filler."
                            )
                        
                        system_instruction += "\nDO NOT merge lines. Return ONLY the exact format: [ID] corrected text."
                        
                        payload = {
                            "system_instruction": {"parts": [{"text": system_instruction}]},
                            "contents": [{"parts": [{"text": prompt_text}]}],
                            "generationConfig": {"temperature": 0.1}
                        }
                        
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{attempt_model}:generateContent?key={gemini_api_key}"
                        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
                        
                        # 5-minute timeout with rate-limit retry
                        res_data = None
                        for api_try in range(3):
                            try:
                                with urllib.request.urlopen(req, timeout=300, context=get_ssl_context()) as response:
                                    res_data = json.loads(response.read().decode('utf-8'))
                                break
                            except urllib.error.HTTPError as he:
                                if he.code == 429 and api_try < 2:
                                    time.sleep(3 * (api_try + 1))
                                    continue
                                raise he

                        if res_data and "candidates" in res_data and len(res_data["candidates"]) > 0:
                            result_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                            
                            import re
                            pattern = re.compile(r'\[(\d+)\]\s*(.*)')
                            matches = pattern.findall(result_text)

                            if not matches and len(batch) > 0:
                                print(f"[STATUS: AI Grammar format error, skipping batch...]", flush=True)
                                # Continues loop, preserving original Whisper segments
                                continue
                            
                            for match_id_str, corrected_text in matches:
                                match_id = int(match_id_str)
                                if 0 <= match_id < len(segments):
                                    seg = segments[match_id]
                                    
                                    class CleanedSegment:
                                        def __init__(self, original, new_text):
                                            self.start = original.start
                                            self.end = original.end
                                            self.text = new_text
                                            self.words = getattr(original, 'words', None)
                                        def __getattr__(self, name):
                                            return getattr(self._original, name)
                                            
                                    cs = CleanedSegment(seg, corrected_text.strip())
                                    cs._original = seg
                                    
                                    # Evenly distribute new words OR inherit original timings
                                    new_words_list = corrected_text.strip().split()
                                    if getattr(seg, 'words', None) is not None:
                                        if len(new_words_list) > 0:
                                            old_words = seg.words
                                            
                                            class DummyWord:
                                                def __init__(self, w, s, e):
                                                    self.word = w
                                                    self.start = s
                                                    self.end = e
                                            
                                            import string, difflib
                                            new_words_obj = []
                                            
                                            def naive_romanize(word):
                                                consonants = {'क':'k','ख':'kh','ग':'g','घ':'gh','ङ':'n','च':'ch','छ':'chh','ज':'j','झ':'jh','ञ':'n','ट':'t','ठ':'th','ड':'d','ढ':'dh','ण':'n','त':'t','थ':'th','द':'d','ध':'dh','न':'n','प':'p','फ':'f','ब':'b','भ':'bh','म':'m','य':'y','र':'r','ल':'l','व':'v','श':'sh','ष':'sh','स':'s','ह':'h','क्ष':'ksh','त्र':'tr','ज्ञ':'gy','श्र':'shr','क़':'q','ख़':'kh','ग़':'g','ज़':'z','ड़':'d','ढ़':'dh','फ़':'f'}
                                                vowels = {'अ':'a','आ':'aa','इ':'i','ई':'ee','उ':'u','ऊ':'oo','ऋ':'ri','ए':'e','ऐ':'ai','ओ':'o','औ':'au','अं':'an','अः':'ah','ऑ':'o','ॉ':'o'}
                                                matras = {'ा':'aa','ि':'i','ी':'ee','ु':'u','ू':'oo','ृ':'ri','े':'e','ै':'ai','ो':'o','ौ':'au','ं':'n','ः':'h','ँ':'n','्':'','़':''}
                                                res = []
                                                for c in word:
                                                    if c in vowels: res.append(vowels[c])
                                                    elif c in consonants: res.append(consonants[c])
                                                    elif c in matras: res.append(matras[c])
                                                    else: res.append(c)
                                                return "".join(res)
                                            
                                            old_clean = []
                                            for w in old_words:
                                                w_str = w.word.strip(string.punctuation).lower()
                                                if is_hinglish_mode: w_str = naive_romanize(w_str)
                                                old_clean.append(w_str)
                                            
                                            new_clean = [w.strip(string.punctuation).lower() for w in new_words_list]
                                            
                                            mapped_starts = [None] * len(new_words_list)
                                            mapped_ends = [None] * len(new_words_list)
                                            
                                            old_i = 0
                                            for new_i, new_w in enumerate(new_clean):
                                                best_match_idx = -1
                                                best_ratio = 0
                                                for j in range(old_i, min(old_i + 3, len(old_clean))):
                                                    ratio = difflib.SequenceMatcher(None, new_w, old_clean[j]).ratio()
                                                    if ratio > 0.6 and ratio > best_ratio:
                                                        best_ratio = ratio
                                                        best_match_idx = j
                                                        
                                                if best_match_idx != -1:
                                                    mapped_starts[new_i] = old_words[best_match_idx].start
                                                    mapped_ends[new_i] = old_words[best_match_idx].end
                                                    old_i = best_match_idx + 1
                                                    
                                            seg_start = old_words[0].start if len(old_words) > 0 else seg.start
                                            seg_end = old_words[-1].end if len(old_words) > 0 else seg.end
                                            
                                            for i in range(len(new_words_list)):
                                                if mapped_starts[i] is None:
                                                    prev_time = None
                                                    prev_idx = -1
                                                    for j in range(i - 1, -1, -1):
                                                        if mapped_ends[j] is not None:
                                                            prev_time = mapped_ends[j]
                                                            prev_idx = j
                                                            break

                                                    next_time = None
                                                    next_idx = len(new_words_list)
                                                    for j in range(i + 1, len(new_words_list)):
                                                        if mapped_starts[j] is not None:
                                                            next_time = mapped_starts[j]
                                                            next_idx = j
                                                            break

                                                    gap_size = next_idx - prev_idx - 1
                                                    idx_in_gap = i - prev_idx - 1

                                                    if prev_idx == -1 and next_idx == len(new_words_list):
                                                        # No words matched at all in entire segment
                                                        total_dur = max(0.3, seg_end - seg_start)
                                                        w_dur = max(0.20, min(0.65, total_dur / len(new_words_list)))
                                                        mapped_starts[i] = seg_start + (i * w_dur)
                                                        mapped_ends[i] = seg_start + ((i + 1) * w_dur)
                                                    elif prev_idx == -1:
                                                        # Prefix words (before first matched word)
                                                        lead_space = max(0.0, next_time - seg_start)
                                                        if lead_space >= gap_size * 0.20:
                                                            w_dur = min(0.50, lead_space / gap_size)
                                                            mapped_starts[i] = next_time - (gap_size - idx_in_gap) * w_dur
                                                            mapped_ends[i] = next_time - (gap_size - idx_in_gap - 1) * w_dur
                                                        else:
                                                            w_dur = 0.28
                                                            mapped_starts[i] = max(0.0, next_time - (gap_size - idx_in_gap) * w_dur)
                                                            mapped_ends[i] = max(0.0, next_time - (gap_size - idx_in_gap - 1) * w_dur)
                                                    elif next_idx == len(new_words_list):
                                                        # Suffix words (after last matched word)
                                                        trail_space = max(0.0, seg_end - prev_time)
                                                        if trail_space >= gap_size * 0.20:
                                                            w_dur = min(0.50, trail_space / gap_size)
                                                            mapped_starts[i] = prev_time + (idx_in_gap * w_dur)
                                                            mapped_ends[i] = prev_time + ((idx_in_gap + 1) * w_dur)
                                                        else:
                                                            w_dur = 0.28
                                                            mapped_starts[i] = prev_time + (idx_in_gap * w_dur)
                                                            mapped_ends[i] = prev_time + ((idx_in_gap + 1) * w_dur)
                                                    else:
                                                        # Internal gap between two matched words
                                                        gap_dur = next_time - prev_time
                                                        if gap_dur >= gap_size * 0.18:
                                                            w_dur = min(0.60, gap_dur / gap_size)
                                                            mapped_starts[i] = prev_time + (idx_in_gap * w_dur)
                                                            mapped_ends[i] = prev_time + ((idx_in_gap + 1) * w_dur)
                                                        else:
                                                            # Tight space or zero gap: synthesize safe slots
                                                            w_dur = 0.22
                                                            mapped_starts[i] = prev_time + (idx_in_gap * w_dur)
                                                            mapped_ends[i] = prev_time + ((idx_in_gap + 1) * w_dur)

                                            # Monotonicity & duration sanity pass
                                            for k in range(len(new_words_list)):
                                                if mapped_ends[k] - mapped_starts[k] < 0.15:
                                                    mapped_ends[k] = round(mapped_starts[k] + 0.22, 3)
                                                elif mapped_ends[k] - mapped_starts[k] > 1.25:
                                                    mapped_ends[k] = round(mapped_starts[k] + 1.25, 3)

                                                if k > 0 and mapped_starts[k] < mapped_ends[k - 1]:
                                                    mapped_starts[k] = mapped_ends[k - 1]
                                                    if mapped_ends[k] <= mapped_starts[k]:
                                                        mapped_ends[k] = round(mapped_starts[k] + 0.20, 3)

                                            for j, w in enumerate(new_words_list):
                                                new_words_obj.append(DummyWord(w, mapped_starts[j], mapped_ends[j]))
                                                
                                            cs.words = new_words_obj
                                            if len(new_words_obj) > 0:
                                                cs.start = new_words_obj[0].start
                                                cs.end = new_words_obj[-1].end
                                        else:
                                            cs.words = []
                                            
                                    segments[match_id] = cs
                    success_ai = True
                    if is_hinglish_mode:
                        gemini_handled_hinglish = True
                    break # Success on this model, break out of fallback loop
                except urllib.error.HTTPError as e:
                    print(f"[STATUS: Model {attempt_model} API error ({e.code}), retrying...]", flush=True)
                    try:
                        err_body = e.read().decode('utf-8')
                        sys.stderr.write(f"[Gemini API Error] {e.code}: {err_body}\n")
                        sys.stderr.flush()
                    except Exception:
                        pass
                    continue
                except urllib.error.URLError as e:
                    print(f"[STATUS: Network unreachable, aborting AI Grammar Corrector...]", flush=True)
                    sys.stderr.write(f"[Gemini Network Error] {str(e)}\n")
                    sys.stderr.flush()
                    break
                except Exception as e:
                    print(f"[STATUS: Model {attempt_model} failed, retrying...]", flush=True)
                    sys.stderr.write(f"[Gemini Error] {type(e).__name__}: {str(e)}\n")
                    traceback.print_exc(file=sys.stderr)
                    sys.stderr.flush()
                    continue
                    
            if not success_ai:
                print("[STATUS: AI Grammar Corrector failed, using fallback...]", flush=True)


        # 3. Process captions
        print("[STATUS: Processing Text...]", flush=True)
        print("[PROGRESS: 80]", flush=True)
        raw_words = []
        for segment in segments:
            if getattr(segment, 'words', None) and len(segment.words) > 0:
                for w in segment.words:
                    raw_words.append({
                        "start": w.start,
                        "end": w.end,
                        "word": w.word
                    })
            else:
                raw_words.append({
                    "start": segment.start,
                    "end": segment.end,
                    "word": segment.text.strip()
                })
                
        # Re-align timestamps to remove the 0.2s pad
        for w in raw_words:
            w['start'] = max(0.0, w['start'] - pad)
            w['end'] = max(0.0, w['end'] - pad)
            
        # Deduplicate consecutive identical words caused by stutters or hallucinations (3+ repetitions or overlapping phantom tokens)
        import re
        deduped_words = []
        last_clean_word = None
        consecutive_repeat_count = 0
        for w in raw_words:
            clean = re.sub(r'[^\w\s]', '', w['word']).strip().lower()
            if clean and clean == last_clean_word:
                consecutive_repeat_count += 1
                if consecutive_repeat_count > 2:
                    continue
                if len(deduped_words) > 0 and (w['start'] < deduped_words[-1]['end'] - 0.05 or (w['end'] - w['start'] < 0.15)):
                    continue
            else:
                last_clean_word = clean
                consecutive_repeat_count = 1
            deduped_words.append(w)
        raw_words = deduped_words

        # Clamp individual word durations to realistic spoken limits (prevents Whisper cross-attention smearing / stretched layers)
        for w in raw_words:
            orig_dur = w['end'] - w['start']
            clean_len = len(str(w.get('word', '')).strip())
            max_w_dur = 0.9 if clean_len <= 4 else 1.25
            if orig_dur > max_w_dur:
                w['end'] = max(round(w['start'] + max_w_dur, 2), w['start'] + 0.1)
            elif orig_dur < 0.15:
                w['end'] = round(w['start'] + 0.20, 2)

        # Monotonicity check on raw_words to guarantee strictly non-overlapping sequence
        for idx in range(1, len(raw_words)):
            if raw_words[idx]['start'] < raw_words[idx - 1]['end'] - 0.02:
                raw_words[idx]['start'] = raw_words[idx - 1]['end']
                if raw_words[idx]['end'] <= raw_words[idx]['start']:
                    raw_words[idx]['end'] = round(raw_words[idx]['start'] + 0.18, 2)

        # Drop isolated phantom micro-tokens (< 0.22s duration surrounded by >= 0.7s silence, only when other words exist)
        if len(raw_words) > 1:
            filtered_words = []
            for i, w in enumerate(raw_words):
                w_dur = w['end'] - w['start']
                prev_gap = (w['start'] - raw_words[i-1]['end']) if i > 0 else 999.0
                next_gap = (raw_words[i+1]['start'] - w['end']) if i < len(raw_words)-1 else 999.0
                if w_dur < 0.22 and prev_gap >= 0.7 and next_gap >= 0.7:
                    continue
                filtered_words.append(w)
            raw_words = filtered_words
            
        # Clean foreign script from individual words for Hindi and Marathi
        if detected_lang in ["hi", "mr"]:
            import re, unicodedata
            foreign_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\u0370-\u03FF\u0590-\u05FF\u0530-\u058F\u0100-\u024F]')
            devanagari_pattern = re.compile(r'[\u0900-\u097F\u1CD0-\u1CFF\uA8E0-\uA8FF]')
            
            cleaned_words = []
            for w in raw_words:
                word_text = unicodedata.normalize('NFC', w['word']).strip()
                has_foreign = bool(foreign_pattern.search(word_text))
                has_devanagari = bool(devanagari_pattern.search(word_text))
                
                if has_foreign and has_devanagari:
                    # Mixed: strip foreign chars, keep Devanagari (e.g. "कु՛"->"कु")
                    cleaned = foreign_pattern.sub('', word_text)
                    if cleaned.strip():
                        w['word'] = cleaned
                        cleaned_words.append(w)
                elif has_foreign and not has_devanagari:
                    # Entirely foreign script word — drop it
                    continue
                elif has_devanagari:
                    # Real English words NEVER contain Devanagari, so any mix = Whisper error
                    latin_chars = re.findall(r'[a-zA-Z]', word_text)
                    if latin_chars:
                        # Strip all Latin from mixed word (e.g. "तum"->"त", "अbhi"->"अ")
                        cleaned = re.sub(r'[a-zA-Z]', '', word_text)
                        if cleaned.strip():
                            w['word'] = cleaned
                            cleaned_words.append(w)
                    else:
                        cleaned_words.append(w)
                else:
                    # Pure English/numbers/punctuation — keep
                    cleaned_words.append(w)
            raw_words = cleaned_words
            
        # VAD silence trim fix
        if True:
            min_silence = max(0.3, max_gap_seconds * 0.8)
            silences = get_dynamic_silences(temp_wav, min_duration=min_silence)
            adjusted_silences = [(max(0.0, s_start - pad), max(0.0, s_end - pad)) for s_start, s_end in silences]
            
            for w in raw_words:
                for s_start, s_end in adjusted_silences:
                    word_start = w['start']
                    word_end = w['end']
                    
                    if word_start >= s_start and word_end <= s_end:
                        w['start'] = s_end
                        w['end'] = max(s_end + 0.1, word_end)
                        continue
                        
                    if s_start > word_start and s_end < word_end:
                        dur_before = s_start - word_start
                        dur_after = word_end - s_end
                        if dur_before > dur_after:
                            w['end'] = s_start
                        else:
                            w['start'] = s_end
                        continue
                        
                    if word_start >= s_start and word_start < s_end:
                        w['start'] = s_end
                        
                    if word_end > s_start and word_end <= s_end:
                        w['end'] = s_start
                        
                if w['end'] <= w['start']:
                    w['end'] = w['start'] + 0.1

        print("[STATUS: Formatting Captions...]", flush=True)
        print("[PROGRESS: 90]", flush=True)
        if transliterate_to_english and not gemini_handled_hinglish:
            def has_devanagari(text):
                return any('\u0900' <= c <= '\u097F' for c in text)

            # Only run transliteration if there are actually Devanagari characters
            if any(has_devanagari(w['word']) for w in raw_words):
                import urllib.request, urllib.parse, difflib, string
                
                consonants = {
                    'क':'k', 'ख':'kh', 'ग':'g', 'घ':'gh', 'ङ':'n',
                    'च':'ch', 'छ':'chh', 'ज':'j', 'झ':'jh', 'ञ':'n',
                    'ट':'t', 'ठ':'th', 'ड':'d', 'ढ':'dh', 'ण':'n',
                    'त':'t', 'थ':'th', 'द':'d', 'ध':'dh', 'न':'n',
                    'प':'p', 'फ':'f', 'ब':'b', 'भ':'bh', 'म':'m',
                    'य':'y', 'र':'r', 'ल':'l', 'व':'v', 'श':'sh', 'ष':'sh', 'स':'s', 'ह':'h',
                    'क्ष':'ksh', 'त्र':'tr', 'ज्ञ':'gy', 'श्र':'shr',
                    'क़':'q', 'ख़':'kh', 'ग़':'g', 'ज़':'z', 'ड़':'d', 'ढ़':'dh', 'फ़':'f'
                }
                vowels = {'अ':'a', 'आ':'aa', 'इ':'i', 'ई':'ee', 'उ':'u', 'ऊ':'oo', 'ऋ':'ri', 'ए':'e', 'ऐ':'ai', 'ओ':'o', 'औ':'au', 'अं':'an', 'अः':'ah', 'ऑ':'o', 'ॉ':'o'}
                matras = {'ा':'aa', 'ि':'i', 'ी':'ee', 'ु':'u', 'ू':'oo', 'ृ':'ri', 'े':'e', 'ै':'ai', 'ो':'o', 'ौ':'au', 'ं':'n', 'ः':'h', 'ँ':'n', '्':'', '़':''}
                overrides = {'मैं': 'main', 'में': 'me', 'नहीं': 'nahi', 'है': 'hai', 'हैं': 'hain', 'हूँ': 'hu', 'था': 'tha', 'थी': 'thi', 'थे': 'the', 'क्या': 'kya', 'यह': 'yeh', 'वह': 'woh', 'और': 'aur', 'को': 'ko', 'का': 'ka', 'की': 'ki', 'के': 'ke', 'से': 'se', 'भी': 'bhi', 'ही': 'hi', 'तो': 'to', 'कर': 'kar', 'रहा': 'raha', 'रही': 'rahi', 'रहे': 'rahe', 'बहुत': 'bahut', 'मुझे': 'mujhe', 'हमें': 'humein', 'तुम': 'tum', 'हम': 'hum', 'आप': 'aap', 'करना': 'karna', 'हो': 'ho', 'गया': 'gaya', 'गई': 'gayi', 'गए': 'gaye', 'अपने': 'apne', 'अपनी': 'apni', 'अपना': 'apna'}
                
                def fallback_trans(word):
                    out_words = []
                    for w in word.split():
                        clean_word = w.strip(string.punctuation + '।॥|')
                        if not clean_word:
                            out_words.append(w.replace('।', '.').replace('॥', '.').replace('|', '.'))
                            continue
                        prefix = w[:w.find(clean_word)]
                        suffix = w[w.find(clean_word)+len(clean_word):]
                        suffix = suffix.replace('।', '.').replace('॥', '.').replace('|', '.')
                        if clean_word in overrides:
                            out_words.append(prefix + overrides[clean_word] + suffix)
                            continue
                        res = []
                        n = len(clean_word)
                        for i in range(n):
                            c = clean_word[i]
                            if c in vowels: res.append(vowels[c])
                            elif c in consonants:
                                res.append(consonants[c])
                                if i < n - 1:
                                    nxt = clean_word[i+1]
                                    if nxt not in matras and nxt not in vowels:
                                        drop_schwa = False
                                        if i > 0 and i < n - 2:
                                            nxt2 = clean_word[i+2]
                                            if nxt2 in matras or nxt2 in vowels:
                                                drop_schwa = True
                                        if not drop_schwa:
                                            res.append('a')
                            elif c in matras:
                                if c == 'ा' and i == n - 1: res.append('a')
                                elif c == 'ी' and i == n - 1: res.append('i')
                                elif c == 'ू' and i == n - 1: res.append('u')
                                else: res.append(matras[c])
                            else: res.append(c)
                        out_words.append(prefix + "".join(res) + suffix)
                    return " ".join(out_words)
                
                def fetch_api(chunk_words):
                    text = " | ".join(chunk_words)
                    quoted_text = urllib.parse.quote(text)
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                    
                    clients = ['gtx', 'dict-chrome-ex']
                    for client in clients:
                        try:
                            url = f'https://translate.googleapis.com/translate_a/single?client={client}&sl=hi&tl=en&dt=rm&dt=t&q={quoted_text}'
                            req = urllib.request.Request(url, headers=headers)
                            with urllib.request.urlopen(req, timeout=5.0, context=get_ssl_context()) as res:
                                data = json.loads(res.read().decode('utf-8'))
                                
                            romanization = ""
                            if data and len(data) > 0 and data[0]:
                                for c in data[0]:
                                    if len(c) > 3 and c[3]:
                                        romanization += c[3]
                                        
                            if romanization and '|' in romanization:
                                parts = [p.strip() for p in romanization.split('|')]
                                if len(parts) == len(chunk_words):
                                    return parts
                            elif len(chunk_words) == 1 and romanization:
                                return [romanization.strip()]
                        except Exception:
                            continue
                    return None

                punct = string.punctuation + '।॥|'
                clean_words = []
                prefixes = []
                suffixes = []
                devanagari_indices = []
                devanagari_query_words = []

                for i, w in enumerate(raw_words):
                    w_str = w['word'].strip()
                    clean_w = w_str.strip(punct)
                    
                    if not clean_w:
                        clean_words.append("")
                        prefixes.append("")
                        suffixes.append(w_str.replace('।', '.').replace('॥', '.').replace('|', '.'))
                        continue
                        
                    idx = w_str.find(clean_w)
                    prefix = w_str[:idx].replace('।', '.').replace('॥', '.').replace('|', '.')
                    suffix = w_str[idx+len(clean_w):].replace('।', '.').replace('॥', '.').replace('|', '.')
                    
                    clean_words.append(clean_w)
                    prefixes.append(prefix)
                    suffixes.append(suffix)

                    if has_devanagari(clean_w):
                        devanagari_indices.append(i)
                        devanagari_query_words.append(clean_w)

                if devanagari_query_words:
                    chunk_size = 20
                    dev_results = []
                    for c_idx in range(0, len(devanagari_query_words), chunk_size):
                        chunk = devanagari_query_words[c_idx:c_idx+chunk_size]
                        res = fetch_api(chunk)
                        if res and len(res) == len(chunk):
                            dev_results.extend(res)
                        else:
                            # Sub-chunk retry with 5-word chunks for 100% precision
                            sub_ok = True
                            sub_res = []
                            sub_size = 5
                            for s_idx in range(0, len(chunk), sub_size):
                                schunk = chunk[s_idx:s_idx+sub_size]
                                sres = fetch_api(schunk)
                                if sres and len(sres) == len(schunk):
                                    sub_res.extend(sres)
                                else:
                                    sub_ok = False
                                    break
                            if sub_ok and len(sub_res) == len(chunk):
                                dev_results.extend(sub_res)
                            else:
                                # Instant offline rule-based phonetic fallback if network fails
                                dev_results.extend([fallback_trans(cw) for cw in chunk])

                    for mapped_idx, orig_i in enumerate(devanagari_indices):
                        if mapped_idx < len(dev_results):
                            clean_words[orig_i] = dev_results[mapped_idx]

                for i, w in enumerate(raw_words):
                    if not clean_words[i]:
                        w['word'] = suffixes[i]
                    else:
                        w['word'] = prefixes[i] + clean_words[i] + suffixes[i]
        # Chunk words
        captions = []
        current_chunk = []
        
        # Japanese and Chinese do not use spaces between words and need char-based chunking
        detected_lang = info.language if info and hasattr(info, 'language') else language
        is_cjk = detected_lang in ["ja", "zh", "ko", "th"]
        join_char = "" if is_cjk else " "
        target_chars = words_per_caption * 4 if is_cjk else 0
        
        def emit_caption(chunk):
            if not chunk:
                return
            text = join_char.join([cw['word'].strip() for cw in chunk])
            cap_start = chunk[0]['start']
            cap_end = chunk[-1]['end']
            
            # Bound caption duration: never exceed readable natural limit (max 3.2s total, or ~0.85s/word)
            max_cap_duration = min(3.2, max(1.2, len(chunk) * 0.85))
            cap_end = min(cap_end, round(cap_start + max_cap_duration, 2))
            
            # Ensure minimum legible duration on screen
            if cap_end - cap_start < 0.35:
                cap_end = round(cap_start + 0.35, 2)
                
            # Ensure each word inside the chunk stays safely within [cap_start, cap_end]
            for cw in chunk:
                if cw['end'] > cap_end:
                    cw['end'] = cap_end
                if cw['start'] >= cw['end']:
                    cw['start'] = max(cap_start, round(cw['end'] - 0.1, 2))
                
            captions.append({
                "start": cap_start,
                "end": cap_end,
                "text": text,
                "words": chunk
            })
        
        for w in raw_words:
            if current_chunk:
                if w['start'] - current_chunk[-1]['end'] > max_gap_seconds:
                    emit_caption(current_chunk)
                    current_chunk = []
                    
            current_chunk.append(w)
            
            should_emit = False
            if is_cjk:
                current_text = join_char.join([cw['word'].strip() for cw in current_chunk])
                if len(current_text) >= target_chars:
                    should_emit = True
            else:
                if len(current_chunk) >= words_per_caption:
                    should_emit = True
                    
            if should_emit:
                emit_caption(current_chunk)
                current_chunk = []
                
        if current_chunk:
            emit_caption(current_chunk)
            
        # Actually fill the timeline gaps visually by extending caption end times (only across short conversational gaps)
        if fill_silence_gaps and len(captions) > 1:
            for i in range(len(captions) - 1):
                gap = captions[i+1]['start'] - captions[i]['end']
                if 0 < gap <= max_gap_seconds:
                    captions[i]['end'] = captions[i+1]['start']
                    if captions[i].get('words') and len(captions[i]['words']) > 0:
                        captions[i]['words'][-1]['end'] = captions[i]['end']
            
        return {"success": True, "captions": captions}
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        # Cleanup
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except:
                pass

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Invalid configuration parameters."}))
        sys.exit(1)
        
    config_path = sys.argv[1]
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # Validate execution session handshake
    session_check = verify_execution_session(config)
    if not session_check.get("valid"):
        print(json.dumps({"error": session_check.get("error", "Please activate a valid QuickSub Pro license.")}))
        sys.exit(1)
        
    result = generate_captions(config)
    
    if "error" not in result:
        print("[STATUS: Finishing...]", flush=True)
        print("[PROGRESS: 100]", flush=True)
        output_json = config.get("output_json")
        if output_json:
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(result.get('captions', []), f)
            print(json.dumps({"success": True, "saved_to": output_json}))
        else:
            print(json.dumps(result))
    else:
        print(json.dumps(result))

if __name__ == '__main__':
    main()
