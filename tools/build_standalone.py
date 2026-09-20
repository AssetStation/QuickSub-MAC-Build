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
                        src = os.path.join(msvc_dlls_dir, f)
                        dst = os.path.join(ENGINE_DIR, f)
                        if not os.path.exists(dst):
                            shutil.copy2(src, dst)
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

if __name__ == "__main__":
    if "--package-cuda-zip" in sys.argv:
        package_cuda_zip()
    else:
        inc_cuda = "--include-cuda" in sys.argv
        build_standalone(include_cuda=inc_cuda)
