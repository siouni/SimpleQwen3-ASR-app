@echo off
setlocal

rem ============================================
rem Qwen3-ASR portable installer (uv route)
rem Windows 11 x64 前提
rem Phase 1:
rem   - GPU 事前確認
rem   - uv の導入
rem   - Python 3.12 の導入
rem   - .venv の作成
rem PyTorch は別フェーズで実施
rem ============================================

set "ROOT_DIR=%~dp0"
set "RUNTIME_DIR=%ROOT_DIR%runtime"
set "DOWNLOAD_DIR=%RUNTIME_DIR%\downloads"
set "UV_DIR=%RUNTIME_DIR%\uv"
set "CACHE_DIR=%RUNTIME_DIR%\cache"
set "UV_CACHE_DIR=%CACHE_DIR%\uv"
set "UV_PYTHON_INSTALL_DIR=%RUNTIME_DIR%\python"
set "UV_PROJECT_ENVIRONMENT=%ROOT_DIR%\.venv"

set "UV_VERSION=0.11.2"
set "UV_ARCHIVE=uv-x86_64-pc-windows-msvc.zip"
set "UV_URL=https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/%UV_ARCHIVE%"
set "UV_ZIP_PATH=%DOWNLOAD_DIR%\%UV_ARCHIVE%"
set "UV_EXTRACT_DIR=%DOWNLOAD_DIR%\uv_extract"
set "UV_EXE=%UV_DIR%\uv.exe"
set "VENV_PYTHON=%UV_PROJECT_ENVIRONMENT%\Scripts\python.exe"
set "GIT_VERSION=2.52.0"
set "GIT_RELEASE_TAG=v2.52.0.windows.1"
set "GIT_ARCHIVE=MinGit-2.52.0-64-bit.zip"
set "GIT_URL=https://github.com/git-for-windows/git/releases/download/%GIT_RELEASE_TAG%/%GIT_ARCHIVE%"
set "GIT_DIR=%RUNTIME_DIR%\git"
set "GIT_ZIP_PATH=%DOWNLOAD_DIR%\%GIT_ARCHIVE%"
set "GIT_EXTRACT_DIR=%DOWNLOAD_DIR%\git_extract"
set "GIT_EXE=%GIT_DIR%\cmd\git.exe"

set "REPO_URL=https://github.com/siouni/SimpleQwen3-ASR-app.git"
set "REPO_BRANCH=main"
set "REPO_TMP_PARENT=%RUNTIME_DIR%\repo_tmp"
set "REPO_TMP_DIR=%REPO_TMP_PARENT%\SimpleQwen3-ASR-app"

set "PYTHON_REQUEST=3.12"
set "GPU_CHECK_TMP=%TEMP%\qwen_asr_gpu_check.txt"

rem ============================================
rem GPU 事前確認
rem ============================================

echo.
echo [GPU CHECK 1/3] GPU 名を確認

where powershell.exe >nul 2>nul
if errorlevel 1 call :FAIL "powershell.exe が見つかりません。"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" > "%GPU_CHECK_TMP%"
if errorlevel 1 call :FAIL "GPU 情報の取得に失敗しました。"

type "%GPU_CHECK_TMP%"

findstr /i "NVIDIA RTX" "%GPU_CHECK_TMP%" >nul
if errorlevel 1 (
    echo [INFO] このツールは NVIDIA GPU 前提です。
    call :FAIL "NVIDIA RTX 系GPUが見つかりませんでした。"
)

findstr /r /i "RTX 40[0-9][0-9]" "%GPU_CHECK_TMP%" >nul
if errorlevel 1 (
    echo [WARN] RTX 40xx らしき表記は見つかりませんでした。
    echo [WARN] ただし NVIDIA RTX GPU であれば続行可能な場合があります。
) else (
    echo [INFO] RTX 40xx 系らしきGPU表記を確認しました。
)

echo.
echo [GPU CHECK 2/3] nvidia-smi を確認

if exist "C:\Windows\System32\nvidia-smi.exe" (
    set "NVIDIA_SMI=C:\Windows\System32\nvidia-smi.exe"
) else (
    where nvidia-smi.exe >nul 2>nul
    if errorlevel 1 (
        echo [INFO] NVIDIA ドライバが未導入か、正常に認識されていない可能性があります。
        call :FAIL "nvidia-smi.exe が見つかりません。"
    )
    for /f "delims=" %%I in ('where nvidia-smi.exe') do set "NVIDIA_SMI=%%I"
)

"%NVIDIA_SMI%" -L
if errorlevel 1 (
    echo [INFO] NVIDIA ドライバ状態を確認してください。
    call :FAIL "nvidia-smi の実行に失敗しました。"
)

echo.
echo [GPU CHECK 3/3] 事前確認OK
echo [INFO] GPU と NVIDIA ドライバは概ね利用可能です。
echo [INFO] CUDA の最終確認は PyTorch 導入フェーズで行ってください。

call :CLEANUP_GPU_TMP


echo.
echo [補助処理 1/4] Git コマンドの確認

where git.exe >nul 2>nul
if errorlevel 1 (
    if exist "%GIT_EXE%" (
        echo [INFO] runtime 配下の Git を使用します。
    ) else (
        echo [INFO] Git が見つからないため MinGit を runtime に配置します。

        if not exist "%GIT_DIR%" mkdir "%GIT_DIR%"
        if exist "%GIT_EXTRACT_DIR%" rmdir /s /q "%GIT_EXTRACT_DIR%"
        mkdir "%GIT_EXTRACT_DIR%"

        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
            "Invoke-WebRequest -Uri '%GIT_URL%' -OutFile '%GIT_ZIP_PATH%'"
        if errorlevel 1 call :FAIL "MinGit のダウンロードに失敗しました。"

        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
            "Expand-Archive -LiteralPath '%GIT_ZIP_PATH%' -DestinationPath '%GIT_EXTRACT_DIR%' -Force"
        if errorlevel 1 call :FAIL "MinGit の展開に失敗しました。"

        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
            "Copy-Item -Path '%GIT_EXTRACT_DIR%\*' -Destination '%GIT_DIR%' -Recurse -Force"
        if errorlevel 1 call :FAIL "MinGit の配置に失敗しました。"

        if not exist "%GIT_EXE%" call :FAIL "git.exe の配置に失敗しました。"
    )

    set "PATH=%GIT_DIR%\cmd;%GIT_DIR%\mingw64\bin;%PATH%"
    echo [INFO] Git Path: %GIT_EXE%
) else (
    for /f "delims=" %%I in ('where git.exe') do set "GIT_EXE=%%I"
    echo [INFO] system Git Path: %GIT_EXE%
)

echo.
echo [補助処理 2/4] Git コマンドの動作確認

"%GIT_EXE%" --version
if errorlevel 1 call :FAIL "git コマンドの実行確認に失敗しました。"

echo.
echo [補助処理 3/4] アプリ本体ファイルの確認

if exist "%ROOT_DIR%app.py" if exist "%ROOT_DIR%launch_app.bat" (
    echo [INFO] app.py / launch_app.bat は揃っています。
    goto :AFTER_ENSURE_REPO
)

echo [INFO] アプリ本体ファイルが不足しているため、GitHub から取得します。

if exist "%REPO_TMP_PARENT%" rmdir /s /q "%REPO_TMP_PARENT%"
mkdir "%REPO_TMP_PARENT%"

"%GIT_EXE%" clone --depth 1 --branch %REPO_BRANCH% "%REPO_URL%" "%REPO_TMP_DIR%"
if errorlevel 1 call :FAIL "アプリ本体の clone に失敗しました。"

if not exist "%ROOT_DIR%app.py" if exist "%REPO_TMP_DIR%\app.py" copy /y "%REPO_TMP_DIR%\app.py" "%ROOT_DIR%app.py" >nul
if not exist "%ROOT_DIR%launch_app.bat" if exist "%REPO_TMP_DIR%\launch_app.bat" copy /y "%REPO_TMP_DIR%\launch_app.bat" "%ROOT_DIR%launch_app.bat" >nul

if not exist "%ROOT_DIR%app.py" call :FAIL "app.py の再配置に失敗しました。"
if not exist "%ROOT_DIR%launch_app.bat" call :FAIL "launch_app.bat の再配置に失敗しました。"

rmdir /s /q "%REPO_TMP_PARENT%"
echo [INFO] 不足していたアプリ本体ファイルを再配置しました。
:AFTER_ENSURE_REPO
echo.
echo [補助処理 4/4] アプリ本体ファイルの確認完了


rem ============================================
rem uv / Python / .venv 構築
rem ============================================

echo.
echo [環境構築 1/8] 既存状態の確認

if exist "%UV_EXE%" (
    echo [INFO] uv は既に配置済みです。
    echo [INFO] Skip: uv download
    echo [INFO] Skip: uv extract
) else (
    echo [INFO] uv は未配置です。
)

if exist "%VENV_PYTHON%" (
    echo [INFO] .venv は既に作成済みです。
    echo [INFO] Skip: venv create
) else (
    echo [INFO] .venv は未作成です。
)

if exist "%UV_EXE%" if exist "%VENV_PYTHON%" goto :VERIFY

echo.
echo [環境構築 2/8] 必要コマンドの確認

where powershell.exe >nul 2>nul
if errorlevel 1 call :FAIL "powershell.exe が見つかりません。"

if not exist "%UV_EXE%" (
    if exist "%UV_ZIP_PATH%" (
        echo [INFO] uv ZIP は既にダウンロード済みです。
        echo [INFO] Skip: curl check
    ) else (
        where curl.exe >nul 2>nul
        if errorlevel 1 (
            set "HAS_CURL=0"
            echo [INFO] curl.exe は見つかりませんでした。PowerShell でダウンロードします。
        ) else (
            set "HAS_CURL=1"
            echo [INFO] curl.exe が見つかりました。
        )
    )
)

echo.
echo [環境構築 3/8] フォルダ作成

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
if not exist "%DOWNLOAD_DIR%" mkdir "%DOWNLOAD_DIR%"
if not exist "%UV_DIR%" mkdir "%UV_DIR%"
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"
if not exist "%UV_CACHE_DIR%" mkdir "%UV_CACHE_DIR%"
if not exist "%UV_PYTHON_INSTALL_DIR%" mkdir "%UV_PYTHON_INSTALL_DIR%"

if not exist "%UV_EXE%" goto :ENSURE_UV
goto :SET_ENV

:ENSURE_UV
echo.
echo [環境構築 4/8] uv ZIP の取得

if exist "%UV_ZIP_PATH%" (
    echo [INFO] 既にダウンロード済みです: %UV_ZIP_PATH%
) else (
    if "%HAS_CURL%"=="1" (
        curl.exe -L --fail --output "%UV_ZIP_PATH%" "%UV_URL%"
        if errorlevel 1 call :FAIL "curl で uv ZIP のダウンロードに失敗しました。"
    ) else (
        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
            "Invoke-WebRequest -Uri '%UV_URL%' -OutFile '%UV_ZIP_PATH%'"
        if errorlevel 1 call :FAIL "PowerShell で uv ZIP のダウンロードに失敗しました。"
    )
)

echo.
echo [環境構築 5/8] uv ZIP の展開

if exist "%UV_EXTRACT_DIR%" rmdir /s /q "%UV_EXTRACT_DIR%"
mkdir "%UV_EXTRACT_DIR%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -LiteralPath '%UV_ZIP_PATH%' -DestinationPath '%UV_EXTRACT_DIR%' -Force"
if errorlevel 1 call :FAIL "uv ZIP の展開に失敗しました。"

if exist "%UV_EXTRACT_DIR%\uv.exe" (
    copy /y "%UV_EXTRACT_DIR%\uv.exe" "%UV_DIR%\uv.exe" >nul
) else (
    for /r "%UV_EXTRACT_DIR%" %%F in (uv.exe) do (
        copy /y "%%F" "%UV_DIR%\uv.exe" >nul
        goto :UV_COPIED
    )
    call :FAIL "展開後に uv.exe が見つかりませんでした。"
)

:UV_COPIED
if exist "%UV_EXTRACT_DIR%\uvx.exe" copy /y "%UV_EXTRACT_DIR%\uvx.exe" "%UV_DIR%\uvx.exe" >nul

if not exist "%UV_EXE%" call :FAIL "uv.exe の配置に失敗しました。"

:SET_ENV
echo.
echo [環境構築 6/8] uv 用の環境変数を設定

set "UV_CACHE_DIR=%UV_CACHE_DIR%"
set "UV_PYTHON_INSTALL_DIR=%UV_PYTHON_INSTALL_DIR%"
set "UV_PROJECT_ENVIRONMENT=%UV_PROJECT_ENVIRONMENT%"

echo [INFO] UV_CACHE_DIR=%UV_CACHE_DIR%
echo [INFO] UV_PYTHON_INSTALL_DIR=%UV_PYTHON_INSTALL_DIR%
echo [INFO] UV_PROJECT_ENVIRONMENT=%UV_PROJECT_ENVIRONMENT%

echo.
echo [環境構築 7/8] Python と仮想環境の作成

if not exist "%VENV_PYTHON%" (
    "%UV_EXE%" python install %PYTHON_REQUEST%
    if errorlevel 1 call :FAIL "uv python install %PYTHON_REQUEST% に失敗しました。"

    "%UV_EXE%" venv "%UV_PROJECT_ENVIRONMENT%" --python %PYTHON_REQUEST%
    if errorlevel 1 call :FAIL "uv venv の作成に失敗しました。"
) else (
    echo [INFO] .venv は既に存在するためスキップします。
)

:VERIFY
echo.
echo [環境構築 8/8] 結果確認

if not exist "%UV_EXE%" call :FAIL "uv.exe が見つかりません: %UV_EXE%"
if not exist "%VENV_PYTHON%" call :FAIL "仮想環境の Python が見つかりません: %VENV_PYTHON%"

echo [環境構築 OK] uv と Python 環境の準備が完了しています。
echo [INFO] uv Path: %UV_EXE%
echo [INFO] venv Python: %VENV_PYTHON%

rem ============================================
rem PyTorch 固定設定
rem 後から変更しやすいように、ここだけまとめておく
rem ============================================
set "TORCH_VERSION=2.7.0"
set "TORCHVISION_VERSION=0.22.0"
set "TORCHAUDIO_VERSION=2.7.0"
set "TORCH_BACKEND=cu128"
set "TORCH_CUDA_VERSION=12.8"

echo.
echo [PyTorch 1/4] 既存状態の確認

"%VENV_PYTHON%" -c "import sys, torch, torchvision, torchaudio; ok=(torch.__version__.split('+')[0]=='%TORCH_VERSION%' and torchvision.__version__.split('+')[0]=='%TORCHVISION_VERSION%' and torchaudio.__version__.split('+')[0]=='%TORCHAUDIO_VERSION%' and torch.cuda.is_available() and str(torch.version.cuda)=='%TORCH_CUDA_VERSION%'); sys.exit(0 if ok else 1)" >nul 2>nul
if errorlevel 1 (
    echo [INFO] 指定した PyTorch 構成ではないか、CUDA が利用できません。
    echo [INFO] PyTorch %TORCH_VERSION% + %TORCH_BACKEND% をインストールまたは再構成します。
    goto :INSTALL_PYTORCH
) else (
    echo [INFO] 指定した PyTorch 構成は既に導入済みです。
    echo [INFO] Skip: PyTorch install
    goto :CHECK_PYTORCH_RESULT
)

:INSTALL_PYTORCH
echo.
echo [PyTorch 2/4] PyTorch のインストール
echo [INFO] torch==%TORCH_VERSION%
echo [INFO] torchvision==%TORCHVISION_VERSION%
echo [INFO] torchaudio==%TORCHAUDIO_VERSION%
echo [INFO] backend=%TORCH_BACKEND%

"%UV_EXE%" pip install ^
    --python "%VENV_PYTHON%" ^
    "torch==%TORCH_VERSION%" ^
    "torchvision==%TORCHVISION_VERSION%" ^
    "torchaudio==%TORCHAUDIO_VERSION%" ^
    --torch-backend=%TORCH_BACKEND%
if errorlevel 1 call :FAIL "PyTorch %TORCH_VERSION% + %TORCH_BACKEND% のインストールに失敗しました。"

:CHECK_PYTORCH_RESULT
echo.
echo [PyTorch 3/4] PyTorch の動作確認

"%VENV_PYTHON%" -c "import torch, torchvision, torchaudio; print('torch=' + torch.__version__); print('torchvision=' + torchvision.__version__); print('torchaudio=' + torchaudio.__version__); print('cuda_available=' + str(torch.cuda.is_available())); print('cuda_version=' + str(torch.version.cuda)); print('device_count=' + str(torch.cuda.device_count())); print('device_0=' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() and torch.cuda.device_count() > 0 else 'N/A'))"
if errorlevel 1 call :FAIL "PyTorch の確認に失敗しました。"

"%VENV_PYTHON%" -c "import sys, torch, torchvision, torchaudio; ok=(torch.__version__.split('+')[0]=='%TORCH_VERSION%' and torchvision.__version__.split('+')[0]=='%TORCHVISION_VERSION%' and torchaudio.__version__.split('+')[0]=='%TORCHAUDIO_VERSION%' and torch.cuda.is_available() and str(torch.version.cuda)=='%TORCH_CUDA_VERSION%'); sys.exit(0 if ok else 1)"
if errorlevel 1 call :FAIL "PyTorch は導入されましたが、指定した構成（%TORCH_VERSION% / %TORCH_BACKEND%）または CUDA 利用条件を満たしていません。"

echo.
echo [PyTorch 4/4] 完了
echo [INFO] PyTorch %TORCH_VERSION% + %TORCH_BACKEND% の準備が完了しました。

rem ============================================
rem FlashAttention2 固定設定
rem 後から変更しやすいように、ここだけまとめておく
rem ============================================
set "FLASH_ATTN_VERSION=2.8.2"
set "FLASH_ATTN_WHEEL_URL=https://github.com/mjun0812/flash-attention-prebuild-wheels/releases/download/v0.4.10/flash_attn-2.8.2+cu128torch2.7-cp312-cp312-win_amd64.whl"

echo.
echo [FlashAttention2 1/4] 既存状態の確認

"%VENV_PYTHON%" -c "import sys, torch, flash_attn; ok=(flash_attn.__version__=='%FLASH_ATTN_VERSION%' and torch.__version__.split('+')[0]=='%TORCH_VERSION%' and torch.cuda.is_available() and str(torch.version.cuda)=='%TORCH_CUDA_VERSION%'); sys.exit(0 if ok else 1)" >nul 2>nul
if errorlevel 1 (
    echo [INFO] 指定した FlashAttention2 構成ではないか、利用条件を満たしていません。
    echo [INFO] FlashAttention2 %FLASH_ATTN_VERSION% をインストールまたは再構成します。
    goto :INSTALL_FLASH_ATTN
) else (
    echo [INFO] 指定した FlashAttention2 構成は既に導入済みです。
    echo [INFO] Skip: FlashAttention2 install
    goto :CHECK_FLASH_ATTN_RESULT
)

:INSTALL_FLASH_ATTN
echo.
echo [FlashAttention2 2/4] FlashAttention2 のインストール
echo [INFO] flash_attn==%FLASH_ATTN_VERSION%
echo [INFO] wheel=%FLASH_ATTN_WHEEL_URL%

"%UV_EXE%" pip install ^
    --python "%VENV_PYTHON%" ^
    "%FLASH_ATTN_WHEEL_URL%"
if errorlevel 1 call :FAIL "FlashAttention2 %FLASH_ATTN_VERSION% のインストールに失敗しました。"

:CHECK_FLASH_ATTN_RESULT
echo.
echo [FlashAttention2 3/4] FlashAttention2 の動作確認

"%VENV_PYTHON%" -c "import torch, flash_attn; print('flash_attn=' + flash_attn.__version__); print('torch=' + torch.__version__); print('cuda_available=' + str(torch.cuda.is_available())); print('cuda_version=' + str(torch.version.cuda)); print('device_count=' + str(torch.cuda.device_count())); print('device_0=' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() and torch.cuda.device_count() > 0 else 'N/A'))"
if errorlevel 1 call :FAIL "FlashAttention2 の確認に失敗しました。"

"%VENV_PYTHON%" -c "import sys, torch, flash_attn; ok=(flash_attn.__version__=='%FLASH_ATTN_VERSION%' and torch.__version__.split('+')[0]=='%TORCH_VERSION%' and torch.cuda.is_available() and str(torch.version.cuda)=='%TORCH_CUDA_VERSION%'); sys.exit(0 if ok else 1)"
if errorlevel 1 call :FAIL "FlashAttention2 は導入されましたが、指定した構成（%FLASH_ATTN_VERSION% / torch %TORCH_VERSION% / %TORCH_BACKEND%）または CUDA 利用条件を満たしていません。"

echo.
echo [FlashAttention2 4/4] 完了
echo [INFO] FlashAttention2 %FLASH_ATTN_VERSION% の準備が完了しました。

rem ============================================
rem Qwen 系 固定設定
rem 1=実行 / 0=スキップ
rem ============================================
set "INSTALL_QWEN3_ASR=1"
set "INSTALL_QWEN3_FORCED_ALIGNER=1"
set "INSTALL_QWEN3_TTS=0"

set "QWEN3_ASR_PACKAGE_SPEC=qwen-asr"
set "QWEN3_TTS_PACKAGE_SPEC=qwen-tts"
set "HF_CLI_PACKAGE_SPEC=huggingface_hub[cli]"

set "MODELS_DIR=%ROOT_DIR%models"
set "HF_HOME=%CACHE_DIR%\huggingface"
set "HF_EXE=%UV_PROJECT_ENVIRONMENT%\Scripts\hf.exe"

set "QWEN3_ASR_MODEL_ID=Qwen/Qwen3-ASR-1.7B"
set "QWEN3_ASR_MODEL_DIR=%MODELS_DIR%\Qwen3-ASR-1.7B"

set "QWEN3_FORCED_ALIGNER_MODEL_ID=Qwen/Qwen3-ForcedAligner-0.6B"
set "QWEN3_FORCED_ALIGNER_MODEL_DIR=%MODELS_DIR%\Qwen3-ForcedAligner-0.6B"

rem TTS は用途で差し替えやすいように変数化
rem 例:
rem   Qwen/Qwen3-TTS-12Hz-1.7B-Base
rem   Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
rem   Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
set "QWEN3_TTS_MODEL_ID=Qwen/Qwen3-TTS-12Hz-1.7B-Base"
set "QWEN3_TTS_MODEL_DIR=%MODELS_DIR%\Qwen3-TTS-12Hz-1.7B-Base"

set "QWEN3_TTS_TOKENIZER_MODEL_ID=Qwen/Qwen3-TTS-Tokenizer-12Hz"
set "QWEN3_TTS_TOKENIZER_MODEL_DIR=%MODELS_DIR%\Qwen3-TTS-Tokenizer-12Hz"

echo.
echo [Qwen 1/7] フォルダと Hugging Face キャッシュの準備

if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%"
if not exist "%HF_HOME%" mkdir "%HF_HOME%"

set "NEED_HF_CLI=0"
if "%INSTALL_QWEN3_ASR%"=="1" set "NEED_HF_CLI=1"
if "%INSTALL_QWEN3_FORCED_ALIGNER%"=="1" set "NEED_HF_CLI=1"
if "%INSTALL_QWEN3_TTS%"=="1" set "NEED_HF_CLI=1"

echo [INFO] MODELS_DIR=%MODELS_DIR%
echo [INFO] HF_HOME=%HF_HOME%

echo.
echo [Qwen 2/7] Hugging Face CLI の確認

if "%NEED_HF_CLI%"=="1" (
    if exist "%HF_EXE%" (
        echo [INFO] hf は既に導入済みです。
        echo [INFO] Skip: huggingface_hub install
    ) else (
        echo [INFO] huggingface_hub をインストールします。
        "%UV_EXE%" pip install ^
            --python "%VENV_PYTHON%" ^
            "%HF_CLI_PACKAGE_SPEC%"
        if errorlevel 1 call :FAIL "huggingface_hub のインストールに失敗しました。"

        if not exist "%HF_EXE%" call :FAIL "hf.exe が見つかりません。"
    )
) else (
    echo [INFO] すべての Qwen インストールが無効です。
    echo [INFO] Skip: huggingface_hub install
)

echo.
echo [Qwen 3/7] qwen-asr runtime の確認

if "%INSTALL_QWEN3_ASR%"=="1" goto :ENSURE_QWEN_ASR_RUNTIME
if "%INSTALL_QWEN3_FORCED_ALIGNER%"=="1" goto :ENSURE_QWEN_ASR_RUNTIME
echo [INFO] Skip: qwen-asr runtime
goto :AFTER_QWEN_ASR_RUNTIME

:ENSURE_QWEN_ASR_RUNTIME
"%VENV_PYTHON%" -c "from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner" >nul 2>nul
if errorlevel 1 (
    echo [INFO] qwen-asr をインストールします。
    "%UV_EXE%" pip install ^
        --python "%VENV_PYTHON%" ^
        "%QWEN3_ASR_PACKAGE_SPEC%"
    if errorlevel 1 call :FAIL "qwen-asr のインストールに失敗しました。"

    "%VENV_PYTHON%" -c "from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner" >nul 2>nul
    if errorlevel 1 call :FAIL "qwen-asr の import 確認に失敗しました。"
) else (
    echo [INFO] qwen-asr は既に導入済みです。
    echo [INFO] Skip: qwen-asr install
)

:AFTER_QWEN_ASR_RUNTIME
echo.
echo [Qwen 4/7] Qwen3-ASR モデルの確認

if "%INSTALL_QWEN3_ASR%"=="1" (
    if exist "%QWEN3_ASR_MODEL_DIR%\*.json" (
        echo [INFO] Qwen3-ASR モデルは既に配置済みです。
        echo [INFO] Skip: Qwen3-ASR model download
    ) else (
        echo [INFO] %QWEN3_ASR_MODEL_ID% をダウンロードします。
        "%HF_EXE%" download "%QWEN3_ASR_MODEL_ID%" --local-dir "%QWEN3_ASR_MODEL_DIR%"
        if errorlevel 1 call :FAIL "Qwen3-ASR モデルのダウンロードに失敗しました。"

        if not exist "%QWEN3_ASR_MODEL_DIR%\*.json" call :FAIL "Qwen3-ASR モデル配置の確認に失敗しました。"
    )
) else (
    echo [INFO] Skip: Qwen3-ASR model download
)

echo.
echo [Qwen 5/7] Qwen3-ForcedAligner モデルの確認

if "%INSTALL_QWEN3_FORCED_ALIGNER%"=="1" (
    if exist "%QWEN3_FORCED_ALIGNER_MODEL_DIR%\*.json" (
        echo [INFO] Qwen3-ForcedAligner モデルは既に配置済みです。
        echo [INFO] Skip: Qwen3-ForcedAligner model download
    ) else (
        echo [INFO] %QWEN3_FORCED_ALIGNER_MODEL_ID% をダウンロードします。
        "%HF_EXE%" download "%QWEN3_FORCED_ALIGNER_MODEL_ID%" --local-dir "%QWEN3_FORCED_ALIGNER_MODEL_DIR%"
        if errorlevel 1 call :FAIL "Qwen3-ForcedAligner モデルのダウンロードに失敗しました。"

        if not exist "%QWEN3_FORCED_ALIGNER_MODEL_DIR%\*.json" call :FAIL "Qwen3-ForcedAligner モデル配置の確認に失敗しました。"
    )
) else (
    echo [INFO] Skip: Qwen3-ForcedAligner model download
)

echo.
echo [Qwen 6/7] qwen-tts runtime の確認

if "%INSTALL_QWEN3_TTS%"=="1" (
    "%VENV_PYTHON%" -c "from qwen_tts import Qwen3TTSModel, Qwen3TTSTokenizer" >nul 2>nul
    if errorlevel 1 (
        echo [INFO] qwen-tts をインストールします。
        "%UV_EXE%" pip install ^
            --python "%VENV_PYTHON%" ^
            "%QWEN3_TTS_PACKAGE_SPEC%"
        if errorlevel 1 call :FAIL "qwen-tts のインストールに失敗しました。"

        "%VENV_PYTHON%" -c "from qwen_tts import Qwen3TTSModel, Qwen3TTSTokenizer" >nul 2>nul
        if errorlevel 1 call :FAIL "qwen-tts の import 確認に失敗しました。"
    ) else (
        echo [INFO] qwen-tts は既に導入済みです。
        echo [INFO] Skip: qwen-tts install
    )
) else (
    echo [INFO] Skip: qwen-tts install
)

echo.
echo [Qwen 7/7] Qwen3-TTS tokenizer / model の確認

if "%INSTALL_QWEN3_TTS%"=="1" (
    if exist "%QWEN3_TTS_TOKENIZER_MODEL_DIR%\*.json" (
        echo [INFO] Qwen3-TTS tokenizer は既に配置済みです。
        echo [INFO] Skip: Qwen3-TTS tokenizer download
    ) else (
        echo [INFO] %QWEN3_TTS_TOKENIZER_MODEL_ID% をダウンロードします。
        "%HF_EXE%" download "%QWEN3_TTS_TOKENIZER_MODEL_ID%" --local-dir "%QWEN3_TTS_TOKENIZER_MODEL_DIR%"
        if errorlevel 1 call :FAIL "Qwen3-TTS tokenizer のダウンロードに失敗しました。"

        if not exist "%QWEN3_TTS_TOKENIZER_MODEL_DIR%\*.json" call :FAIL "Qwen3-TTS tokenizer 配置の確認に失敗しました。"
    )

    if exist "%QWEN3_TTS_MODEL_DIR%\*.json" (
        echo [INFO] Qwen3-TTS モデルは既に配置済みです。
        echo [INFO] Skip: Qwen3-TTS model download
    ) else (
        echo [INFO] %QWEN3_TTS_MODEL_ID% をダウンロードします。
        "%HF_EXE%" download "%QWEN3_TTS_MODEL_ID%" --local-dir "%QWEN3_TTS_MODEL_DIR%"
        if errorlevel 1 call :FAIL "Qwen3-TTS モデルのダウンロードに失敗しました。"

        if not exist "%QWEN3_TTS_MODEL_DIR%\*.json" call :FAIL "Qwen3-TTS モデル配置の確認に失敗しました。"
    )
) else (
    echo [INFO] Skip: Qwen3-TTS tokenizer / model download
)

echo.
echo [Qwen OK] Qwen3-ASR / Qwen3-ForcedAligner / Qwen3-TTS の処理が完了しました。

rem ============================================
rem PySide6 固定設定
rem 1=実行 / 0=スキップ
rem ============================================
set "INSTALL_PYSIDE6=1"
set "PYSIDE6_VERSION=6.9.1"
set "PYSIDE6_PACKAGE_SPEC=PySide6==%PYSIDE6_VERSION%"

echo.
echo [PySide6 1/4] 既存状態の確認

if "%INSTALL_PYSIDE6%"=="1" (
    "%VENV_PYTHON%" -c "import sys, PySide6; ok=(PySide6.__version__=='%PYSIDE6_VERSION%'); sys.exit(0 if ok else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [INFO] 指定した PySide6 構成ではないため、インストールまたは再構成します。
        goto :INSTALL_PYSIDE6
    ) else (
        echo [INFO] 指定した PySide6 構成は既に導入済みです。
        echo [INFO] Skip: PySide6 install
        goto :CHECK_PYSIDE6_RESULT
    )
) else (
    echo [INFO] Skip: PySide6 install
    goto :AFTER_PYSIDE6
)

:INSTALL_PYSIDE6
echo.
echo [PySide6 2/4] PySide6 のインストール
echo [INFO] %PYSIDE6_PACKAGE_SPEC%

"%UV_EXE%" pip install ^
    --python "%VENV_PYTHON%" ^
    "%PYSIDE6_PACKAGE_SPEC%"
if errorlevel 1 call :FAIL "PySide6 %PYSIDE6_VERSION% のインストールに失敗しました。"

:CHECK_PYSIDE6_RESULT
echo.
echo [PySide6 3/4] PySide6 の確認

"%VENV_PYTHON%" -c "import PySide6, PySide6.QtCore; print('PySide6=' + PySide6.__version__); print('Qt=' + PySide6.QtCore.qVersion())"
if errorlevel 1 call :FAIL "PySide6 の確認に失敗しました。"

"%VENV_PYTHON%" -c "import sys, PySide6; ok=(PySide6.__version__=='%PYSIDE6_VERSION%'); sys.exit(0 if ok else 1)"
if errorlevel 1 call :FAIL "PySide6 は導入されましたが、指定した構成（%PYSIDE6_VERSION%）を満たしていません。"

echo.
echo [PySide6 4/4] 完了
echo [INFO] PySide6 %PYSIDE6_VERSION% の準備が完了しました。

:AFTER_PYSIDE6

echo.
pause
endlocal
exit /b 0

:CLEANUP_GPU_TMP
if exist "%GPU_CHECK_TMP%" del /q "%GPU_CHECK_TMP%" >nul 2>nul
exit /b 0

:FAIL
echo.
echo [ERROR] %~1
call :CLEANUP_GPU_TMP
echo.
pause
endlocal
exit /b 1
