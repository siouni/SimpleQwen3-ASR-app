# SimpleQwen3-ASR

SimpleQwen3-ASR は、PySide6 ベースのシンプルな GUI で Qwen3-ASR と Qwen3-ForcedAligner を扱うためのローカルアプリケーションです。

現在の `app.py` では、以下の流れを 1 画面で扱えます。

- 音声ファイルのドラッグ&ドロップ読み込み
- 音声のプレビュー再生
- 音声の自動分割
- 分割単位での Qwen3-ASR 実行
- Qwen3-ForcedAligner によるタイムスタンプ生成
- 分割セグメントごとの ASR 結果表示
- 再生位置に追従するタイムスタンプリストのハイライト

## 動作環境

- Windows 11 64bit
- NVIDIA GeForce RTX 40xx 系 GPU
- NVIDIA ドライバが正しく導入され、`nvidia-smi` が利用可能であること
- Python 3.12 系
- PyTorch は CUDA 対応版を別途導入する想定

このリポジトリの `setup.bat` は、上記環境を前提にセットアップする構成になっています。

## 前提条件

- `requirements.txt` を使う方法では、PyTorch は別途ユーザー側でインストールすること

## モデル配置

`app.py` はローカルモデルを明示的に参照します。

- ASR モデル: `.\models\Qwen3-ASR-1.7B`
- ForcedAligner モデル: `.\models\Qwen3-ForcedAligner-0.6B`

参考: 現在の `app.py` では未使用ですが、今後の拡張向けに以下の TTS 系モデル配置も想定します。

- TTS モデル: `.\models\Qwen3-TTS-12Hz-1.7B-Base`
- TTS Tokenizer: `.\models\Qwen3-TTS-Tokenizer-12Hz`

## インストール方法

### 1. `setup.bat` を使う方法

セットアップ済みのバッチで環境構築する方法です。

実行前に、できるだけ短いパスで、英字のみを使い、スペースや記号を含まないフォルダを作成し、その中に `setup.bat` を置いて実行することを推奨します。

`setup.bat` は、実行した bat ファイルと同じ階層を基準に `.venv`、`runtime`、`models` などの各種ファイルを保存します。

`setup.bat` だけを取得したい場合は、GitHub から直接ダウンロードできます。

- 閲覧: `https://github.com/siouni/SimpleQwen3-ASR-app/blob/main/setup.bat`
- ダウンロード: `https://raw.githubusercontent.com/siouni/SimpleQwen3-ASR-app/main/setup.bat`

```powershell
.\setup.bat
```

この方法では、`uv`、Python 仮想環境、PyTorch、Qwen 系ランタイム、モデルダウンロードまでまとめて構成する想定です。

### 2. `uv` と `requirements.txt` を使う方法

`uv` が既にユーザー環境にインストール済みで、PyTorch を別途インストールする前提で、アプリ実行に必要な Python パッケージのみを入れる方法です。

まず仮想環境を用意します。

```powershell
uv venv .venv --python 3.12
```

次に仮想環境を有効化します。

```powershell
.venv\Scripts\Activate
```

その後、PyTorch をインストールします。

例: PyTorch 2.7.0 + cu128

```powershell
uv pip install torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 --torch-backend=cu128
```

必要に応じて、FlashAttention2 もインストールします。

例: Python 3.12 + torch 2.7 + cu128

```powershell
uv pip install https://github.com/mjun0812/flash-attention-prebuild-wheels/releases/download/v0.4.10/flash_attn-2.8.2+cu128torch2.7-cp312-cp312-win_amd64.whl
```

続けて、PyTorch、FlashAttention2 を除く依存関係をインストールします。

```powershell
uv pip install -r .\requirements.txt
```

次に、`hf` コマンドでモデルをダウンロードします。

```powershell
hf download Qwen/Qwen3-ASR-1.7B --local-dir .\models\Qwen3-ASR-1.7B
hf download Qwen/Qwen3-ForcedAligner-0.6B --local-dir .\models\Qwen3-ForcedAligner-0.6B
```

参考: 現在の `app.py` では未使用ですが、今後の拡張予定として Qwen3-TTS 系モデルを取得する場合は以下を利用できます。

```powershell
hf download Qwen/Qwen3-TTS-12Hz-1.7B-Base --local-dir .\models\Qwen3-TTS-12Hz-1.7B-Base
hf download Qwen/Qwen3-TTS-Tokenizer-12Hz --local-dir .\models\Qwen3-TTS-Tokenizer-12Hz
```

## 起動方法

### 1. 起動用 bat ファイルをダブルクリックする方法

エクスプローラー上で `launch_app.bat` をダブルクリックして起動します。

### 2. PowerShell から起動する方法

```powershell
.\.venv\Scripts\Activate.ps1
python .\app.py
```

## 現在の構成ファイル

- `app.py`: GUI 本体
- `launch_app.bat`: アプリ起動用バッチ
- `setup.bat`: Windows 向けセットアップバッチ
- `requirements.txt`: PyTorch を除く固定バージョン依存関係
