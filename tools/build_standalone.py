import os
import sys
import shutil
import subprocess
import zipfile

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(TOOLS_DIR, "generate_captions.py")
IS_MAC = sys.platform == "darwin"

if IS_MAC:
    import platform
    mac_arch = "arm64" if platform.machine().lower() in ["arm64", "aarch64"] else "x64"
    ENGINE_DIR = os.path.join(TOOLS_DIR, "mac", f"engine_{mac_arch}")
else:
    ENGINE_DIR = os.path.join(TOOLS_DIR, "win", "engine")
BUILD_DIR = os.path.join(TOOLS_DIR, "_build_tmp")
DIST_DIR = os.path.join(TOOLS_DIR, "_dist_tmp")
SPEC_PATH = os.path.join(TOOLS_DIR, "generate_captions.spec")

def package_cuda_zip():
    """Package CUDA 12 and cuDNN runtime DLLs into a standalone zip for on-demand downloading."""
    print("\n--- Packaging CUDA Runtime Zip ---")
    cuda_zip_path = os.path.join(TOOLS_DIR, "cuda-runtime-win-x64.zip")
    
    # Locate CUDA and cuDNN binaries from the local Python environment
    venv_sp = os.path.join(sys.prefix, "Lib", "site-packages")
    cublas_bin = os.path.join(venv_sp, "nvidia", "cublas", "bin")
    cudnn_bin = os.path.join(venv_sp, "nvidia", "cudnn", "bin")
    
    dll_files = []
    if os.path.isdir(cublas_bin):
        for f in os.listdir(cublas_bin):
            if f.lower().endswith(".dll"):
                dll_files.append(os.path.join(cublas_bin, f))
                
    if os.path.isdir(cudnn_bin):
        for f in os.listdir(cudnn_bin):
            if f.lower().endswith(".dll"):
                dll_files.append(os.path.join(cudnn_bin, f))
                
    if not dll_files:
        print("[!] No CUDA DLLs found in python site-packages. Skipping zip creation.")
        return
        
    print(f"[*] Found {len(dll_files)} CUDA/cuDNN DLLs. Compressing into {cuda_zip_path}...")
    with zipfile.ZipFile(cuda_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dll in dll_files:
            arcname = os.path.basename(dll)
            print(f"  + Adding {arcname}")
            zf.write(dll, arcname=arcname)
            
    zip_size_mb = os.path.getsize(cuda_zip_path) / (1024 * 1024)
    print(f"[✓] Successfully created {cuda_zip_path} ({zip_size_mb:.1f} MB)")

def compile_native_msvc_launcher(engine_dir):
    """Compiles a clean native MSVC C++ launcher to replace PyInstaller's generic bootloader
    and configures an isolated official embeddable Python runtime (0% antivirus false positives)."""
    print("\n[*] Configuring isolated official Python runtime & MSVC Launcher (0% antivirus false positives)...")

    # 1. Compile generate_captions.py to bytecode generate_captions.pyc
    import py_compile
    src_py = os.path.join(TOOLS_DIR, "generate_captions.py")
    dst_pyc = os.path.join(engine_dir, "generate_captions.pyc")
    if os.path.exists(src_py):
        try:
            py_compile.compile(src_py, cfile=dst_pyc, doraise=True)
            print(f"[✓] Compiled bytecode engine: {dst_pyc}")
        except Exception as e:
            print(f"[!] Warning: Could not compile pyc: {e}")

    # 2. Copy official signed python.exe to engine directory
    python_src = sys.executable
    python_dst = os.path.join(engine_dir, "python.exe")
    try:
        shutil.copy2(python_src, python_dst)
        print(f"[✓] Bundled official signed Python binary: {python_dst}")
    except Exception as e:
        print(f"[!] Warning: Could not copy python.exe: {e}")

    # 3. Ensure official python311.dll & python3.dll are in engine directory
    internal_dir = os.path.join(engine_dir, "_internal")
    for dll_name in ["python311.dll", "python3.dll"]:
        dst_dll = os.path.join(engine_dir, dll_name)
        if not os.path.exists(dst_dll):
            src_candidate = os.path.join(internal_dir, dll_name)
            if not os.path.exists(src_candidate):
                src_candidate = os.path.join(sys.base_prefix, dll_name)
            if os.path.exists(src_candidate):
                try:
                    shutil.copy2(src_candidate, dst_dll)
                    print(f"[✓] Copied {dll_name} to engine root: {dst_dll}")
                except Exception as e:
                    print(f"[!] Warning copying {dll_name}: {e}")

    # 4. Ensure official python311.zip (embeddable stdlib) is present
    python_zip = os.path.join(engine_dir, "python311.zip")
    if not os.path.exists(python_zip) or os.path.getsize(python_zip) < 1000000:
        print("[*] Retrieving official Python standard library (python311.zip)...")
        cache_zip = os.path.join(TOOLS_DIR, "python311.zip")
        if os.path.exists(cache_zip):
            shutil.copy2(cache_zip, python_zip)
            print(f"[✓] Copied cached python311.zip to engine root: {python_zip}")
        else:
            try:
                import urllib.request, io
                embed_url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
                print(f"[*] Downloading embed package from {embed_url}...")
                with urllib.request.urlopen(embed_url, timeout=30) as resp:
                    embed_bytes = resp.read()
                with zipfile.ZipFile(io.BytesIO(embed_bytes)) as zf:
                    zf.extract("python311.zip", engine_dir)
                    zf.extract("python311.zip", TOOLS_DIR)
                print(f"[✓] Successfully installed official python311.zip ({os.path.getsize(python_zip) // 1024} KB)")
            except Exception as dl_err:
                print(f"[!] Warning: Could not download embed package ({dl_err}). Packing from local Lib...")
                lib_dir = os.path.join(sys.base_prefix, "Lib")
                if os.path.isdir(lib_dir):
                    with zipfile.ZipFile(python_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                        for root, dirs, files in os.walk(lib_dir):
                            dirs[:] = [d for d in dirs if d not in ["site-packages", "test", "idlelib", "tkinter"]]
                            for f in files:
                                if f.endswith((".py", ".pyc")):
                                    full_f = os.path.join(root, f)
                                    arc = os.path.relpath(full_f, lib_dir)
                                    zf.write(full_f, arc)
                    print(f"[✓] Packed local Python standard library to: {python_zip}")

    # 5. Write python311._pth to enforce complete isolated mode
    pth_file = os.path.join(engine_dir, "python311._pth")
    with open(pth_file, "w", encoding="utf-8") as f:
        f.write("python311.zip\n.\n_internal\n")
    print(f"[✓] Configured isolated runtime environment: {pth_file}")

    # 6. Synchronize complete pure-Python package sources into _internal (e.g. av, numpy, filelock, fsspec, yaml, httpx)
    site_packages = os.path.join(sys.base_prefix, "Lib", "site-packages")
    if os.path.isdir(site_packages) and os.path.isdir(internal_dir):
        packages_to_sync = [
            "av", "numpy", "filelock", "fsspec", "packaging", "yaml",
            "httpx", "httpcore", "anyio", "sniffio", "h11", "idna"
        ]
        for pkg in packages_to_sync:
            src_pkg = os.path.join(site_packages, pkg)
            dst_pkg = os.path.join(internal_dir, pkg)
            if os.path.isdir(src_pkg):
                for root, dirs, files in os.walk(src_pkg):
                    rel = os.path.relpath(root, src_pkg)
                    target_dir = os.path.join(dst_pkg, rel)
                    os.makedirs(target_dir, exist_ok=True)
                    for f in files:
                        if f.endswith((".py", ".pyi", ".pyd")):
                            src_file = os.path.join(root, f)
                            dst_file = os.path.join(target_dir, f)
                            if not os.path.exists(dst_file):
                                shutil.copy2(src_file, dst_file)
        # Also copy single-file pure Python modules
        single_modules = ["typing_extensions.py"]
        for mod in single_modules:
            src_mod = os.path.join(site_packages, mod)
            dst_mod = os.path.join(internal_dir, mod)
            if os.path.exists(src_mod) and not os.path.exists(dst_mod):
                shutil.copy2(src_mod, dst_mod)
        print("[✓] Synchronized all runtime package sources into _internal.")

    # 7. Purge any debug CRT DLLs (*d.dll) from engine_dir
    for f in os.listdir(engine_dir):
        if f.lower().endswith("d.dll") and ("msvcp" in f.lower() or "vcruntime" in f.lower()):
            try:
                os.remove(os.path.join(engine_dir, f))
                print(f"[✓] Removed debug CRT binary: {f}")
            except Exception:
                pass

    # 8. Locate Visual Studio vcvars64.bat
    vswhere = os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe")
    vcvars_path = None
    if os.path.exists(vswhere):
        try:
            res = subprocess.run(
                [vswhere, "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                capture_output=True, text=True, check=True
            )
            vs_path = res.stdout.strip()
            cand = os.path.join(vs_path, "VC", "Auxiliary", "Build", "vcvars64.bat")
            if os.path.exists(cand):
                vcvars_path = cand
        except Exception:
            pass

    if not vcvars_path:
        print("[!] Visual Studio C++ compiler not detected. Skipping native launcher compilation.")
        return

    launcher_cpp = os.path.join(TOOLS_DIR, "launcher.cpp")
    launcher_rc = os.path.join(TOOLS_DIR, "launcher.rc")
    out_exe = os.path.join(engine_dir, "generate_captions.exe")

    if not os.path.exists(launcher_cpp) or not os.path.exists(launcher_rc):
        print("[!] Launcher source files not found. Skipping.")
        return

    # 9. Compile with rc.exe and cl.exe
    build_cmd = (
        f'call "{vcvars_path}" && '
        f'cd /d "{TOOLS_DIR}" && '
        f'rc /fo launcher.res launcher.rc && '
        f'cl /O2 /W3 /MT /EHsc /Fe:"{out_exe}" launcher.cpp launcher.res && '
        f'del launcher.obj launcher.res'
    )
    proc = subprocess.run(build_cmd, shell=True, capture_output=True, text=True)
    if proc.returncode == 0 and os.path.exists(out_exe):
        size_kb = os.path.getsize(out_exe) // 1024
        print(f"[✓] Successfully compiled clean native C++ launcher: {out_exe} ({size_kb} KB)")
    else:
        print(f"[!] Warning: Native launcher compilation failed, keeping existing binary:\n{proc.stderr or proc.stdout}")

def build_standalone(include_cuda=False):
    print("=" * 60)
    print("QuickSub Pro - Standalone Engine Compiler")
    print(f"Target OS: {'macOS' if IS_MAC else 'Windows'}")
    print(f"Engine Output Directory: {ENGINE_DIR}")
    print("=" * 60)

    # Clean previous build artifacts
    for p in [BUILD_DIR, DIST_DIR]:
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)
    if os.path.exists(SPEC_PATH):
        try:
            os.remove(SPEC_PATH)
        except Exception:
            pass

    # Use python module invocation for 100% reliable execution
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name", "generate_captions",
        "--workpath", BUILD_DIR,
        "--distpath", DIST_DIR,
        "--specpath", TOOLS_DIR,
        "--collect-all", "ctranslate2",
        "--collect-all", "faster_whisper",
        "--collect-all", "tokenizers",
        "--collect-all", "huggingface_hub",
        "--collect-all", "tqdm",
        "--collect-all", "onnxruntime",
        "--collect-all", "cryptography",
        "--collect-all", "certifi",
        "--console",
    ]

    # Exclude heavy unnecessary packages to keep base engine lightweight (~100 MB)
    excludes = [
        "tkinter", "matplotlib", "scipy", "torch", "unittest",
        "test", "distutils", "setuptools", "wheel"
    ]
    if not include_cuda:
        excludes.extend(["nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"])

    for ex in excludes:
        cmd.extend(["--exclude-module", ex])

    if not IS_MAC:
        version_file = os.path.join(TOOLS_DIR, "version_info.txt")
        if os.path.exists(version_file):
            cmd.extend(["--version-file", version_file])
        ico_file = os.path.join(TOOLS_DIR, "win", "icon.ico")
        if os.path.exists(ico_file):
            cmd.extend(["--icon", ico_file])

    cmd.append(SCRIPT_PATH)

    print(f"[*] Running PyInstaller command:\n{' '.join(cmd)}\n")
    proc = subprocess.run(cmd)

    if proc.returncode != 0:
        print(f"\n[!] PyInstaller build failed with exit code {proc.returncode}")
        sys.exit(proc.returncode)

    built_engine_src = os.path.join(DIST_DIR, "generate_captions")
    if not os.path.isdir(built_engine_src):
        print(f"[!] Output folder not found at {built_engine_src}")
        sys.exit(1)

    # 1. Preserve existing CUDA runtime folder (~1.9 GB) so it doesn't get wiped
    cuda_src_dir = os.path.join(ENGINE_DIR, "cuda")
    cuda_staging_dir = os.path.join(TOOLS_DIR, "_cuda_staging_tmp")
    has_existing_cuda = False

    if os.path.isdir(cuda_src_dir):
        print(f"[*] Found existing CUDA runtime at: {cuda_src_dir}")
        print("[*] Staging CUDA runtime safely to prevent deletion during rebuild...")
        if os.path.exists(cuda_staging_dir):
            shutil.rmtree(cuda_staging_dir, ignore_errors=True)
        shutil.move(cuda_src_dir, cuda_staging_dir)
        has_existing_cuda = True

    # 2. Backup any existing MSVC runtime DLLs to tools/win/dlls if not already present
    if not IS_MAC:
        msvc_dlls_dir = os.path.join(TOOLS_DIR, "win", "dlls")
        os.makedirs(msvc_dlls_dir, exist_ok=True)
        if os.path.isdir(ENGINE_DIR):
            for f in os.listdir(ENGINE_DIR):
                if f.lower().endswith(".dll"):
                    if f.lower().endswith("d.dll") and ("msvcp" in f.lower() or "vcruntime" in f.lower()):
                        continue
                    dll_src = os.path.join(ENGINE_DIR, f)
                    dll_dst = os.path.join(msvc_dlls_dir, f)
                    if not os.path.exists(dll_dst):
                        shutil.copy2(dll_src, dll_dst)

    try:
        # Clean existing destination engine directory
        if os.path.exists(ENGINE_DIR):
            print(f"[*] Cleaning existing destination: {ENGINE_DIR}")
            shutil.rmtree(ENGINE_DIR, ignore_errors=True)

        os.makedirs(os.path.dirname(ENGINE_DIR), exist_ok=True)
        print(f"[*] Deploying compiled engine to: {ENGINE_DIR}")
        shutil.move(built_engine_src, ENGINE_DIR)

        # 3. Restore CUDA runtime pack
        if has_existing_cuda and os.path.isdir(cuda_staging_dir):
            print(f"[✓] Restoring CUDA runtime pack to: {cuda_src_dir}")
            shutil.move(cuda_staging_dir, cuda_src_dir)

        # 4. Bundle standard C++ runtime DLLs from tools/win/dlls
        if not IS_MAC:
            msvc_dlls_dir = os.path.join(TOOLS_DIR, "win", "dlls")
            if os.path.isdir(msvc_dlls_dir):
                print("[*] Ensuring MSVC runtime DLLs are bundled...")
                for f in os.listdir(msvc_dlls_dir):
                    if f.lower().endswith(".dll"):
                        if f.lower().endswith("d.dll") and ("msvcp" in f.lower() or "vcruntime" in f.lower()):
                            continue
                        src = os.path.join(msvc_dlls_dir, f)
                        dst = os.path.join(ENGINE_DIR, f)
                        if not os.path.exists(dst):
                            shutil.copy2(src, dst)

            # 5. Compile clean native C++ launcher (eliminates PyInstaller antivirus false positives)
            compile_native_msvc_launcher(ENGINE_DIR)
    finally:
        # Fallback safety: if an error occurred before restoring CUDA, restore it now
        if has_existing_cuda and os.path.isdir(cuda_staging_dir):
            if not os.path.isdir(cuda_src_dir):
                print("[!] Restoring staged CUDA runtime back to engine folder after unexpected error...")
                os.makedirs(ENGINE_DIR, exist_ok=True)
                shutil.move(cuda_staging_dir, cuda_src_dir)

    # Clean up temporary build dirs
    for p in [BUILD_DIR, DIST_DIR]:
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"[OK] Standalone engine successfully built at:\n    {ENGINE_DIR}")
    exe_name = "generate_captions" if IS_MAC else "generate_captions.exe"
    exe_path = os.path.join(ENGINE_DIR, exe_name)
    if os.path.exists(exe_path):
        size_mb = sum(os.path.getsize(os.path.join(root, file)) for root, dirs, files in os.walk(ENGINE_DIR) for file in files) / (1024 * 1024)
        print(f"[OK] Total engine folder size: {size_mb:.1f} MB (Executable: {os.path.basename(exe_path)})")
    print("=" * 60)

def build_with_nuitka(include_cuda=False):
    print("=" * 60)
    print("QuickSub Pro - Nuitka Native C Machine-Code Compiler")
    print(f"Target OS: {'macOS' if IS_MAC else 'Windows'}")
    print(f"Engine Output Directory: {ENGINE_DIR}")
    print("=" * 60)

    try:
        subprocess.run([sys.executable, "-m", "nuitka", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception:
        print("[*] Installing Nuitka and dependencies for native C compilation...")
        subprocess.run([sys.executable, "-m", "pip", "install", "nuitka", "zstandard"], check=True)

    for p in [BUILD_DIR, DIST_DIR]:
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        f"--output-dir={DIST_DIR}",
        "--include-package=ctranslate2",
        "--include-package=faster_whisper",
        "--include-package=tokenizers",
        "--include-package=huggingface_hub",
        "--include-package=tqdm",
        "--include-package=onnxruntime",
        "--include-package=cryptography",
        "--include-package=certifi",
    ]
    if not IS_MAC:
        cmd.append("--windows-console-mode=force")
        cmd.append("--company-name=QuickSub Pro")
        cmd.append("--product-name=QuickSub Pro")
        cmd.append("--file-version=1.0.0.0")
        cmd.append("--product-version=1.0.0.0")
        cmd.append("--file-description=QuickSub Pro AI Caption Generation Engine")
        cmd.append("--copyright=Copyright (C) 2026 QuickSub Pro")
        ico_file = os.path.join(TOOLS_DIR, "win", "icon.ico")
        if os.path.exists(ico_file):
            cmd.append(f"--windows-icon-from-ico={ico_file}")
    cmd.append(SCRIPT_PATH)

    print(f"[*] Running Nuitka command:\n{' '.join(cmd)}\n")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"\n[!] Nuitka build failed with exit code {proc.returncode}")
        sys.exit(proc.returncode)

    built_engine_src = os.path.join(DIST_DIR, "generate_captions.dist")
    if not os.path.isdir(built_engine_src):
        built_engine_src = os.path.join(DIST_DIR, "generate_captions")
    if not os.path.isdir(built_engine_src):
        print(f"[!] Nuitka output directory not found in {DIST_DIR}")
        sys.exit(1)

    # Preserve existing CUDA runtime folder (~1.9 GB)
    cuda_src_dir = os.path.join(ENGINE_DIR, "cuda")
    cuda_staging_dir = os.path.join(TOOLS_DIR, "_cuda_staging_tmp")
    has_existing_cuda = False
    if os.path.isdir(cuda_src_dir):
        print(f"[*] Found existing CUDA runtime at: {cuda_src_dir}")
        print("[*] Staging CUDA runtime safely to prevent deletion during rebuild...")
        if os.path.exists(cuda_staging_dir):
            shutil.rmtree(cuda_staging_dir, ignore_errors=True)
        shutil.move(cuda_src_dir, cuda_staging_dir)
        has_existing_cuda = True

    try:
        if os.path.exists(ENGINE_DIR):
            shutil.rmtree(ENGINE_DIR, ignore_errors=True)
        os.makedirs(os.path.dirname(ENGINE_DIR), exist_ok=True)
        shutil.move(built_engine_src, ENGINE_DIR)

        if has_existing_cuda and os.path.isdir(cuda_staging_dir):
            print(f"[✓] Restoring CUDA runtime pack to: {cuda_src_dir}")
            shutil.move(cuda_staging_dir, cuda_src_dir)

        if not IS_MAC:
            msvc_dlls_dir = os.path.join(TOOLS_DIR, "win", "dlls")
            if os.path.isdir(msvc_dlls_dir):
                for f in os.listdir(msvc_dlls_dir):
                    if f.lower().endswith(".dll"):
                        src = os.path.join(msvc_dlls_dir, f)
                        dst = os.path.join(ENGINE_DIR, f)
                        if not os.path.exists(dst):
                            shutil.copy2(src, dst)
    finally:
        if has_existing_cuda and os.path.isdir(cuda_staging_dir):
            if not os.path.isdir(cuda_src_dir):
                os.makedirs(ENGINE_DIR, exist_ok=True)
                shutil.move(cuda_staging_dir, cuda_src_dir)

    for p in [BUILD_DIR, DIST_DIR]:
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"[OK] Standalone native machine-code engine built at:\n    {ENGINE_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    if "--package-cuda-zip" in sys.argv:
        package_cuda_zip()
    elif "--launcher-only" in sys.argv:
        compile_native_msvc_launcher(ENGINE_DIR)
    elif "--nuitka" in sys.argv:
        inc_cuda = "--include-cuda" in sys.argv
        build_with_nuitka(include_cuda=inc_cuda)
    else:
        inc_cuda = "--include-cuda" in sys.argv
        build_standalone(include_cuda=inc_cuda)
