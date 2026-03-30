# このコードは PySide6 で音声ファイルのドラッグ&ドロップ読み込みとプレビュー再生を行う簡易 GUI アプリケーションです。
from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QThread, QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QTextEdit,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner


SHORT_AUDIO_THRESHOLD_SECONDS = 35.0
TARGET_SEGMENT_SECONDS = 28.0
MAX_SEGMENT_SECONDS = 32.0
MIN_SEGMENT_SECONDS = 12.0
SEARCH_WINDOW_SECONDS = 4.0
ZERO_CROSS_WINDOW_SECONDS = 0.05
ENERGY_WINDOW_MS = 20
QWEN3_ASR_MODEL_DIR = Path(__file__).resolve().parent / "models" / "Qwen3-ASR-1.7B"
QWEN3_ALIGNER_MODEL_DIR = Path(__file__).resolve().parent / "models" / "Qwen3-ForcedAligner-0.6B"


class AudioSegment:
    def __init__(self, index: int, start_frame: int, end_frame: int, sample_rate: int, channels: int) -> None:
        self.index = index
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.sample_rate = sample_rate
        self.channels = channels

    @property
    def start_seconds(self) -> float:
        return self.start_frame / self.sample_rate

    @property
    def end_seconds(self) -> float:
        return self.end_frame / self.sample_rate

    @property
    def duration_seconds(self) -> float:
        return (self.end_frame - self.start_frame) / self.sample_rate

    def label(self) -> str:
        return (
            f"{self.index + 1:02d}: "
            f"{format_seconds(self.start_seconds)} - {format_seconds(self.end_seconds)} "
            f"({format_seconds(self.duration_seconds)})"
        )


class ASRWorker(QObject):
    progress_changed = Signal(int, int, str)
    segment_finished = Signal(int, str)
    completed = Signal()
    failed = Signal(str)

    def __init__(
        self,
        model_path: str,
        asr_model: Qwen3ASRModel | None,
        audio_data: np.ndarray,
        sample_rate: int,
        segments: list[AudioSegment],
        target_device: str,
    ) -> None:
        super().__init__()
        self.model_path = model_path
        self.asr_model = asr_model
        self.audio_data = audio_data
        self.sample_rate = sample_rate
        self.segments = segments
        self.target_device = target_device

    def run(self) -> None:
        try:
            self.progress_changed.emit(0, len(self.segments), "ASR チェックポイントをロード中")
            model = self.asr_model
            if model is None:
                model = Qwen3ASRModel.from_pretrained(self.model_path, **build_cpu_model_load_kwargs())
                self.asr_model = model
            self.progress_changed.emit(0, len(self.segments), f"ASR モデルを {self.target_device.upper()} に転送中")
            move_asr_model_to_device(model, self.target_device)
            total = len(self.segments)
            for index, segment in enumerate(self.segments):
                self.progress_changed.emit(index, total, f"ASR 実行中: {index + 1}/{total}")
                segment_audio = self.audio_data[segment.start_frame : segment.end_frame]
                result = model.transcribe((segment_audio, self.sample_rate))
                transcription = result[0] if result else None
                text = transcription.text.strip() if transcription else ""
                self.segment_finished.emit(segment.index, text)
            if self.target_device == "gpu":
                self.progress_changed.emit(total, total, "ASR モデルを CPU にオフロード中")
                move_asr_model_to_device(model, "cpu")
            self.progress_changed.emit(total, total, "ASR 完了")
            self.completed.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AlignWorker(QObject):
    progress_changed = Signal(int, int, str)
    segment_finished = Signal(int, object)
    completed = Signal()
    failed = Signal(str)

    def __init__(
        self,
        aligner_path: str,
        forced_aligner: Qwen3ForcedAligner | None,
        audio_data: np.ndarray,
        sample_rate: int,
        segments: list[AudioSegment],
        texts: list[str],
        target_device: str,
    ) -> None:
        super().__init__()
        self.aligner_path = aligner_path
        self.forced_aligner = forced_aligner
        self.audio_data = audio_data
        self.sample_rate = sample_rate
        self.segments = segments
        self.texts = texts
        self.target_device = target_device

    def run(self) -> None:
        try:
            self.progress_changed.emit(0, len(self.segments), "ForcedAligner チェックポイントをロード中")
            aligner = self.forced_aligner
            if aligner is None:
                aligner = Qwen3ForcedAligner.from_pretrained(self.aligner_path, **build_cpu_model_load_kwargs())
                self.forced_aligner = aligner
            self.progress_changed.emit(0, len(self.segments), f"ForcedAligner モデルを {self.target_device.upper()} に転送中")
            move_forced_aligner_to_device(aligner, self.target_device)
            total = len(self.segments)
            for index, segment in enumerate(self.segments):
                text = self.texts[index].strip()
                if not text:
                    self.segment_finished.emit(segment.index, [])
                    continue
                self.progress_changed.emit(index, total, f"強制アライメント実行中: {index + 1}/{total}")
                segment_audio = self.audio_data[segment.start_frame : segment.end_frame]
                align_result = aligner.align((segment_audio, self.sample_rate), text, "ja")
                align_items: list[dict[str, object]] = []
                if align_result:
                    align_items = [
                        {
                            "text": item.text,
                            "start_time": item.start_time,
                            "end_time": item.end_time,
                        }
                        for item in align_result[0].items
                    ]
                self.segment_finished.emit(segment.index, align_items)
            if self.target_device == "gpu":
                self.progress_changed.emit(total, total, "ForcedAligner モデルを CPU にオフロード中")
                move_forced_aligner_to_device(aligner, "cpu")
            self.progress_changed.emit(total, total, "強制アライメント完了")
            self.completed.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AudioDropArea(QFrame):
    def __init__(self, on_file_dropped) -> None:
        super().__init__()
        self._on_file_dropped = on_file_dropped
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        self.heading_label = QLabel("音声ファイルをここにドロップ")
        self.heading_label.setObjectName("dropHeading")

        self.detail_label = QLabel("対応形式: wav / mp3 / m4a / flac / ogg")
        self.detail_label.setObjectName("dropDetail")
        self.detail_label.setWordWrap(True)

        layout.addWidget(self.heading_label)
        layout.addWidget(self.detail_label)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._extract_audio_file(event.mimeData().urls()):
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)

        file_path = self._extract_audio_file(event.mimeData().urls())
        if file_path is None:
            event.ignore()
            return

        self._on_file_dropped(file_path)
        event.acceptProposedAction()

    @staticmethod
    def _extract_audio_file(urls: list[QUrl]) -> Path | None:
        for url in urls:
            if not url.isLocalFile():
                continue
            candidate = Path(url.toLocalFile())
            if candidate.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}:
                return candidate
        return None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_file: Path | None = None
        self.audio_data: np.ndarray | None = None
        self.sample_rate: int = 0
        self.audio_channels: int = 0
        self.segments: list[AudioSegment] = []
        self.segment_texts: list[str] = []
        self.segment_timestamps: list[list[dict[str, object]]] = []
        self.segment_buffers: dict[int, tuple[QByteArray, QBuffer]] = {}
        self.current_preview_segment: AudioSegment | None = None
        self.asr_thread: QThread | None = None
        self.asr_worker: ASRWorker | None = None
        self.align_thread: QThread | None = None
        self.align_worker: AlignWorker | None = None
        self.asr_model_cache: Qwen3ASRModel | None = None
        self.forced_aligner_cache: Qwen3ForcedAligner | None = None
        self.cuda_available = torch.cuda.is_available()
        self.preferred_device = "gpu" if self.cuda_available else "cpu"
        self._slider_is_dragging = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)

        self.setWindowTitle("EasyQwen3 ASR")
        self.resize(960, 640)
        self._build_ui()
        self._connect_player_signals()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        hero = QFrame()
        hero.setObjectName("heroPanel")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 20, 20, 20)
        hero_layout.setSpacing(12)

        self.drop_area = AudioDropArea(self.load_audio_file)
        self.file_meta_label = QLabel("ファイルをドロップするか、選択ボタンを押して下さい。")
        self.file_meta_label.setObjectName("fileMetaLabel")
        self.file_meta_label.setWordWrap(True)
        self.file_meta_label.setContentsMargins(8, 4, 8, 4)

        self.device_label = QLabel("実行デバイス")
        self.device_label.setObjectName("sectionLabel")
        self.device_combo = QComboBox()
        self.device_combo.addItem("GPU", "gpu")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.setCurrentIndex(0 if self.cuda_available else 1)
        if not self.cuda_available:
            self.device_combo.setEnabled(False)
            self.device_combo.setToolTip("torch.cuda.is_available() が False のため CPU 固定です。")

        self.segment_info_label = QLabel("分割リスト: 未生成")
        self.segment_info_label.setObjectName("segmentInfoLabel")
        self.segment_info_label.setContentsMargins(8, 2, 8, 2)

        self.segment_list = QListWidget()
        self.segment_list.setObjectName("segmentList")
        self.segment_list.setMaximumHeight(180)

        self.asr_progress_bar = QProgressBar()
        self.asr_progress_bar.setObjectName("asrProgressBar")
        self.asr_progress_bar.setRange(0, 1)
        self.asr_progress_bar.setValue(0)
        self.asr_progress_bar.setTextVisible(True)
        self.asr_progress_bar.setFormat("待機中")

        self.result_text = QTextEdit()
        self.result_text.setObjectName("resultText")
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("分割リストから項目を選択すると ASR 結果を表示します。")
        self.result_text.setMaximumHeight(180)

        self.timestamp_list = QListWidget()
        self.timestamp_list.setObjectName("timestampList")
        self.timestamp_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.timestamp_list.setFocusPolicy(Qt.NoFocus)
        self.timestamp_list.setMaximumHeight(180)

        result_row = QHBoxLayout()
        result_row.setSpacing(12)
        result_row.addWidget(self.result_text, 1)
        result_row.addWidget(self.timestamp_list, 1)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setEnabled(False)
        self.position_slider.setRange(0, 0)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setObjectName("timeLabel")

        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)
        slider_row.addWidget(self.position_slider, 1)
        slider_row.addWidget(self.time_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.open_button = QPushButton("音声ファイルを選択")
        self.play_button = QPushButton("再生")
        self.stop_button = QPushButton("停止")
        self.run_asr_button = QPushButton("ASR を実行")
        self.run_align_button = QPushButton("強制アライメント")
        self.play_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.run_asr_button.setEnabled(False)
        self.run_align_button.setEnabled(False)
        button_row.addWidget(self.open_button)
        button_row.addWidget(self.play_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.run_asr_button)
        button_row.addWidget(self.run_align_button)
        button_row.addStretch(1)

        hero_layout.addWidget(self.drop_area)
        hero_layout.addWidget(self.file_meta_label)
        hero_layout.addWidget(self.device_label)
        hero_layout.addWidget(self.device_combo)
        hero_layout.addWidget(self.segment_info_label)
        hero_layout.addWidget(self.segment_list)
        hero_layout.addWidget(self.asr_progress_bar)
        hero_layout.addLayout(result_row)
        hero_layout.addLayout(slider_row)
        hero_layout.addLayout(button_row)
        hero_layout.addStretch(1)

        layout.addWidget(hero, 1)

        self.setCentralWidget(root)

        status_bar = QStatusBar(self)
        status_bar.showMessage("モック起動中")
        self.setStatusBar(status_bar)

        self.open_button.clicked.connect(self.open_file_dialog)
        self.play_button.clicked.connect(self.toggle_playback)
        self.stop_button.clicked.connect(self.stop_playback)
        self.run_asr_button.clicked.connect(self.start_asr)
        self.run_align_button.clicked.connect(self.start_alignment)
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.segment_list.currentRowChanged.connect(self._on_segment_selected)

    def _connect_player_signals(self) -> None:
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    def _on_device_changed(self, _index: int) -> None:
        self.preferred_device = self.device_combo.currentData() or "cpu"

    def open_file_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "音声ファイルを選択",
            "",
            "Audio Files (*.wav *.mp3 *.m4a *.flac *.ogg *.aac)",
        )
        if file_path:
            self.load_audio_file(Path(file_path))

    def load_audio_file(self, file_path: Path) -> None:
        self.current_file = file_path
        self._reset_segment_preview()
        self.player.setSource(QUrl.fromLocalFile(str(file_path)))
        self.position_slider.setValue(0)
        self.position_slider.setEnabled(True)
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        self.file_meta_label.setText(f"{file_path} | {file_size_mb:.2f} MB")
        self.time_label.setText("00:00 / 00:00")
        self._load_segments(file_path)
        self.statusBar().showMessage(f"読み込み完了: {file_path.name}")

    def toggle_playback(self) -> None:
        if self.current_file is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop_playback(self) -> None:
        self.player.stop()

    def _on_slider_pressed(self) -> None:
        self._slider_is_dragging = True

    def _on_slider_released(self) -> None:
        self._slider_is_dragging = False
        self.player.setPosition(self.position_slider.value())

    def _on_position_changed(self, position: int) -> None:
        if not self._slider_is_dragging:
            self.position_slider.setValue(position)
        self._update_time_label(position, self.player.duration())

    def _on_duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)
        self._update_time_label(self.player.position(), duration)

    def _on_playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self.play_button.setText("一時停止")
            self.statusBar().showMessage("再生中")
        elif state == QMediaPlayer.PausedState:
            self.play_button.setText("再生")
            self.statusBar().showMessage("一時停止中")
        else:
            self.play_button.setText("再生")
            if self.current_file is not None:
                if self.current_preview_segment is None:
                    self.statusBar().showMessage(f"停止中: {self.current_file.name}")
                else:
                    self.statusBar().showMessage(
                        f"停止中: セグメント {self.current_preview_segment.index + 1}"
                    )

    def _on_player_error(self, _error) -> None:
        message = self.player.errorString() or "音声の読み込みに失敗しました。"
        self.statusBar().showMessage(message)

    def _on_segment_selected(self, row: int) -> None:
        self._refresh_selected_segment_result()
        if row < 0 or row >= len(self.segments):
            self.current_preview_segment = None
            return

        segment = self.segments[row]
        self.current_preview_segment = segment
        self.player.stop()
        self._set_player_source_for_segment(segment)
        self.position_slider.setValue(0)
        self._update_time_label(0, int(segment.duration_seconds * 1000))
        self.statusBar().showMessage(f"セグメント {segment.index + 1} を選択")

    def _load_segments(self, file_path: Path) -> None:
        self.segment_list.clear()
        self.segment_buffers.clear()
        self.current_preview_segment = None
        self.segment_texts = []
        self.result_text.clear()
        self.timestamp_list.clear()
        self.run_asr_button.setEnabled(False)
        self.run_align_button.setEnabled(False)
        self.asr_progress_bar.setRange(0, 1)
        self.asr_progress_bar.setValue(0)
        self.asr_progress_bar.setFormat("分割準備中")

        try:
            audio_data, sample_rate = sf.read(file_path, dtype="float32", always_2d=True)
        except RuntimeError as exc:
            self.audio_data = None
            self.sample_rate = 0
            self.audio_channels = 0
            self.segments = []
            self.segment_texts = []
            self.segment_timestamps = []
            self.segment_info_label.setText("分割リスト: 音声解析に失敗しました")
            self.statusBar().showMessage(f"分割解析に失敗: {exc}")
            return

        self.audio_data = audio_data
        self.sample_rate = sample_rate
        self.audio_channels = audio_data.shape[1]
        self.segments = build_segments(audio_data, sample_rate)
        self.segment_texts = ["" for _ in self.segments]
        self.segment_timestamps = [[] for _ in self.segments]

        for segment in self.segments:
            item = QListWidgetItem(segment.label())
            item.setData(Qt.UserRole, segment.index)
            self.segment_list.addItem(item)

        if len(self.segments) == 1:
            self.segment_info_label.setText("分割リスト: 分割なし")
        else:
            self.segment_info_label.setText(f"分割リスト: {len(self.segments)} セグメント")

        if self.segments:
            self.run_asr_button.setEnabled(True)
            self.run_align_button.setEnabled(True)
            self.asr_progress_bar.setRange(0, len(self.segments))
            self.asr_progress_bar.setValue(0)
            self.asr_progress_bar.setFormat(f"未実行 (0/{len(self.segments)})")
            self.segment_list.setCurrentRow(0)

    def _reset_segment_preview(self) -> None:
        self.player.stop()
        self.segment_buffers.clear()
        self.current_preview_segment = None

    def _set_player_source_for_segment(self, segment: AudioSegment) -> None:
        if self.audio_data is None:
            return

        if segment.index not in self.segment_buffers:
            segment_data = self.audio_data[segment.start_frame : segment.end_frame]
            wav_bytes = create_wav_bytes(segment_data, self.sample_rate)
            byte_array = QByteArray(wav_bytes)
            buffer = QBuffer(self)
            buffer.setData(byte_array)
            buffer.open(QIODevice.ReadOnly)
            self.segment_buffers[segment.index] = (byte_array, buffer)

        _, buffer = self.segment_buffers[segment.index]
        buffer.seek(0)
        self.player.setSourceDevice(buffer, QUrl("segment.wav"))

    def start_asr(self) -> None:
        if self.audio_data is None or not self.segments:
            return
        if not QWEN3_ASR_MODEL_DIR.exists():
            self.statusBar().showMessage(f"モデルが見つかりません: {QWEN3_ASR_MODEL_DIR}")
            self.result_text.setPlainText(
                f"Qwen3-ASR モデルが見つかりません。\n{QWEN3_ASR_MODEL_DIR}"
            )
            return
        if not QWEN3_ALIGNER_MODEL_DIR.exists():
            self.statusBar().showMessage(f"モデルが見つかりません: {QWEN3_ALIGNER_MODEL_DIR}")
            self.result_text.setPlainText(
                f"Qwen3-ForcedAligner モデルが見つかりません。\n{QWEN3_ALIGNER_MODEL_DIR}"
            )
            return
        if self.asr_thread is not None or self.align_thread is not None:
            return

        self.segment_texts = ["" for _ in self.segments]
        self.segment_timestamps = [[] for _ in self.segments]
        self._refresh_selected_segment_result()
        self.run_asr_button.setEnabled(False)
        self.run_align_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.asr_progress_bar.setRange(0, len(self.segments))
        self.asr_progress_bar.setValue(0)
        self.asr_progress_bar.setFormat(f"チェックポイント待機中 (0/{len(self.segments)})")

        self.asr_thread = QThread(self)
        self.asr_worker = ASRWorker(
            str(QWEN3_ASR_MODEL_DIR),
            self.asr_model_cache,
            self.audio_data.copy(),
            self.sample_rate,
            list(self.segments),
            self.preferred_device,
        )
        self.asr_worker.moveToThread(self.asr_thread)

        self.asr_thread.started.connect(self.asr_worker.run)
        self.asr_worker.progress_changed.connect(self._on_asr_progress_changed)
        self.asr_worker.segment_finished.connect(self._on_asr_segment_finished)
        self.asr_worker.completed.connect(self._on_asr_completed)
        self.asr_worker.failed.connect(self._on_asr_failed)
        self.asr_worker.completed.connect(self.asr_thread.quit)
        self.asr_worker.failed.connect(self.asr_thread.quit)
        self.asr_thread.finished.connect(self._cleanup_asr_thread)
        self.asr_thread.start()

    def _on_asr_progress_changed(self, completed_count: int, total_count: int, message: str) -> None:
        self.asr_progress_bar.setRange(0, max(1, total_count))
        self.asr_progress_bar.setValue(completed_count)
        self.asr_progress_bar.setFormat(f"{message}")
        self.statusBar().showMessage(message)

    def _on_asr_segment_finished(self, index: int, text: str) -> None:
        if 0 <= index < len(self.segment_texts):
            self.segment_texts[index] = text
        self.asr_progress_bar.setValue(index + 1)
        self.asr_progress_bar.setFormat(f"ASR 実行中 ({index + 1}/{len(self.segments)})")
        self._refresh_selected_segment_result()

    def _on_asr_completed(self) -> None:
        self.asr_progress_bar.setValue(len(self.segments))
        self.asr_progress_bar.setFormat(f"ASR 完了 ({len(self.segments)}/{len(self.segments)})")
        self.statusBar().showMessage("ASR 完了")
        self.run_asr_button.setEnabled(True)
        self.run_align_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self._refresh_selected_segment_result()

    def _on_asr_failed(self, message: str) -> None:
        self.asr_progress_bar.setFormat("ASR 失敗")
        self.statusBar().showMessage(f"ASR 失敗: {message}")
        self.result_text.setPlainText(f"ASR 実行中にエラーが発生しました。\n{message}")
        self.run_asr_button.setEnabled(True)
        self.run_align_button.setEnabled(True)
        self.open_button.setEnabled(True)

    def _cleanup_asr_thread(self) -> None:
        if self.asr_worker is not None:
            self.asr_model_cache = self.asr_worker.asr_model
            self.asr_worker.deleteLater()
        if self.asr_thread is not None:
            self.asr_thread.deleteLater()
        self.asr_worker = None
        self.asr_thread = None

    def start_alignment(self) -> None:
        if self.audio_data is None or not self.segments:
            return
        if not QWEN3_ALIGNER_MODEL_DIR.exists():
            self.statusBar().showMessage(f"モデルが見つかりません: {QWEN3_ALIGNER_MODEL_DIR}")
            self.result_text.setPlainText(
                f"Qwen3-ForcedAligner モデルが見つかりません。\n{QWEN3_ALIGNER_MODEL_DIR}"
            )
            return
        if self.asr_thread is not None or self.align_thread is not None:
            return

        self.segment_timestamps = [[] for _ in self.segments]
        self._refresh_selected_segment_result()
        self.run_asr_button.setEnabled(False)
        self.run_align_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.asr_progress_bar.setRange(0, len(self.segments))
        self.asr_progress_bar.setValue(0)
        self.asr_progress_bar.setFormat(f"強制アライメント待機中 (0/{len(self.segments)})")

        self.align_thread = QThread(self)
        self.align_worker = AlignWorker(
            str(QWEN3_ALIGNER_MODEL_DIR),
            self.forced_aligner_cache,
            self.audio_data.copy(),
            self.sample_rate,
            list(self.segments),
            list(self.segment_texts),
            self.preferred_device,
        )
        self.align_worker.moveToThread(self.align_thread)

        self.align_thread.started.connect(self.align_worker.run)
        self.align_worker.progress_changed.connect(self._on_align_progress_changed)
        self.align_worker.segment_finished.connect(self._on_align_segment_finished)
        self.align_worker.completed.connect(self._on_align_completed)
        self.align_worker.failed.connect(self._on_align_failed)
        self.align_worker.completed.connect(self.align_thread.quit)
        self.align_worker.failed.connect(self.align_thread.quit)
        self.align_thread.finished.connect(self._cleanup_align_thread)
        self.align_thread.start()

    def _on_align_progress_changed(self, completed_count: int, total_count: int, message: str) -> None:
        self.asr_progress_bar.setRange(0, max(1, total_count))
        self.asr_progress_bar.setValue(completed_count)
        self.asr_progress_bar.setFormat(message)
        self.statusBar().showMessage(message)

    def _on_align_segment_finished(self, index: int, timestamps: list[dict[str, object]]) -> None:
        if 0 <= index < len(self.segment_timestamps):
            self.segment_timestamps[index] = timestamps
        self.asr_progress_bar.setValue(index + 1)
        self.asr_progress_bar.setFormat(f"強制アライメント実行中 ({index + 1}/{len(self.segments)})")
        self._refresh_selected_segment_result()

    def _on_align_completed(self) -> None:
        self.asr_progress_bar.setValue(len(self.segments))
        self.asr_progress_bar.setFormat(f"強制アライメント完了 ({len(self.segments)}/{len(self.segments)})")
        self.statusBar().showMessage("強制アライメント完了")
        self.run_asr_button.setEnabled(True)
        self.run_align_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self._refresh_selected_segment_result()

    def _on_align_failed(self, message: str) -> None:
        self.asr_progress_bar.setFormat("強制アライメント失敗")
        self.statusBar().showMessage(f"強制アライメント失敗: {message}")
        self.result_text.setPlainText(f"強制アライメント実行中にエラーが発生しました。\n{message}")
        self.run_asr_button.setEnabled(True)
        self.run_align_button.setEnabled(True)
        self.open_button.setEnabled(True)

    def _cleanup_align_thread(self) -> None:
        if self.align_worker is not None:
            self.forced_aligner_cache = self.align_worker.forced_aligner
            self.align_worker.deleteLater()
        if self.align_thread is not None:
            self.align_thread.deleteLater()
        self.align_worker = None
        self.align_thread = None

    def _refresh_selected_segment_result(self) -> None:
        row = self.segment_list.currentRow()
        if row < 0 or row >= len(self.segment_texts):
            self.result_text.clear()
            self.timestamp_list.clear()
            return

        text = self.segment_texts[row].strip()
        if text:
            self.result_text.setPlainText(text)
        else:
            self.result_text.setPlainText("このセグメントの ASR 結果はまだありません。")

        self.timestamp_list.clear()
        for item in self.segment_timestamps[row]:
            start_text = format_seconds(float(item["start_time"]))
            end_text = format_seconds(float(item["end_time"]))
            self.timestamp_list.addItem(f"{start_text} - {end_text}  {item['text']}")
        self._update_timestamp_highlight(self.player.position())

    def _update_time_label(self, position: int, duration: int) -> None:
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")
        self._update_timestamp_highlight(position)

    def _update_timestamp_highlight(self, position: int) -> None:
        row = self.segment_list.currentRow()
        if row < 0 or row >= len(self.segment_timestamps):
            for index in range(self.timestamp_list.count()):
                item = self.timestamp_list.item(index)
                item.setBackground(QColor("transparent"))
            return

        timestamp_items = self.segment_timestamps[row]
        active_indexes: list[int] = []
        position_seconds = position / 1000.0
        for index, item in enumerate(timestamp_items):
            start_time = float(item["start_time"])
            end_time = float(item["end_time"])
            if start_time <= position_seconds < end_time:
                active_indexes.append(index)

        for index in range(self.timestamp_list.count()):
            item = self.timestamp_list.item(index)
            is_active = index in active_indexes
            if is_active:
                item.setBackground(QColor("#ead9bf"))
            else:
                item.setBackground(QColor("transparent"))

        if active_indexes:
            current_item = self.timestamp_list.item(active_indexes[0])
            self.timestamp_list.scrollToItem(current_item)

    @staticmethod
    def _format_ms(milliseconds: int) -> str:
        total_seconds = max(0, milliseconds // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


def build_segments(audio_data: np.ndarray, sample_rate: int) -> list[AudioSegment]:
    total_frames = audio_data.shape[0]
    total_seconds = total_frames / sample_rate
    channels = audio_data.shape[1]

    if total_seconds <= SHORT_AUDIO_THRESHOLD_SECONDS:
        return [AudioSegment(0, 0, total_frames, sample_rate, channels)]

    mono = audio_data.mean(axis=1)
    boundaries = [0]
    target_frames = int(TARGET_SEGMENT_SECONDS * sample_rate)
    max_frames = int(MAX_SEGMENT_SECONDS * sample_rate)
    min_frames = int(MIN_SEGMENT_SECONDS * sample_rate)

    while total_frames - boundaries[-1] > max_frames:
        segment_start = boundaries[-1]
        target_frame = min(segment_start + target_frames, total_frames - min_frames)
        next_boundary = find_split_frame(
            mono=mono,
            sample_rate=sample_rate,
            segment_start=segment_start,
            target_frame=target_frame,
            total_frames=total_frames,
            min_frames=min_frames,
            max_frames=max_frames,
        )
        if next_boundary <= segment_start:
            next_boundary = min(segment_start + max_frames, total_frames)
        boundaries.append(next_boundary)

    boundaries.append(total_frames)

    segments: list[AudioSegment] = []
    for index, (start_frame, end_frame) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        segments.append(AudioSegment(index, start_frame, end_frame, sample_rate, channels))
    return segments


def find_split_frame(
    mono: np.ndarray,
    sample_rate: int,
    segment_start: int,
    target_frame: int,
    total_frames: int,
    min_frames: int,
    max_frames: int,
) -> int:
    search_frames = int(SEARCH_WINDOW_SECONDS * sample_rate)
    start = max(segment_start + min_frames, target_frame - search_frames)
    end = min(segment_start + max_frames, target_frame + search_frames, total_frames - min_frames)
    if end <= start:
        return min(segment_start + max_frames, total_frames)

    energy_window = max(1, int(sample_rate * ENERGY_WINDOW_MS / 1000))
    candidate_scores: list[tuple[float, int]] = []
    for center in range(start, end, energy_window):
        left = max(0, center - energy_window // 2)
        right = min(total_frames, center + energy_window // 2)
        frame = mono[left:right]
        if frame.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
        candidate_scores.append((rms, center))

    if not candidate_scores:
        return min(segment_start + max_frames, total_frames)

    candidate_scores.sort(key=lambda item: (item[0], abs(item[1] - target_frame)))
    best_center = candidate_scores[0][1]
    return find_zero_crossing(mono, sample_rate, best_center, start, end)


def find_zero_crossing(
    mono: np.ndarray,
    sample_rate: int,
    center_frame: int,
    min_frame: int,
    max_frame: int,
) -> int:
    radius = int(ZERO_CROSS_WINDOW_SECONDS * sample_rate)
    start = max(min_frame + 1, center_frame - radius)
    end = min(max_frame, center_frame + radius)
    if end <= start:
        return center_frame

    samples = mono[start - 1 : end + 1]
    sign_changes = np.where(np.signbit(samples[:-1]) != np.signbit(samples[1:]))[0]
    if sign_changes.size == 0:
        return center_frame

    zero_crossings = start + sign_changes
    distances = np.abs(zero_crossings - center_frame)
    return int(zero_crossings[np.argmin(distances)])


def create_wav_bytes(audio_data: np.ndarray, sample_rate: int) -> bytes:
    clipped = np.clip(audio_data, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    byte_stream = io.BytesIO()
    with wave.open(byte_stream, "wb") as wav_file:
        wav_file.setnchannels(audio_data.shape[1])
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())
    return byte_stream.getvalue()


def format_seconds(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def build_cpu_model_load_kwargs() -> dict[str, object]:
    return {"device_map": "cpu", "dtype": torch.float32}


def move_asr_model_to_device(model: Qwen3ASRModel, target_device: str) -> None:
    target = "cuda:0" if target_device == "gpu" and torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if target.startswith("cuda") else torch.float32
    model.model.to(device=target, dtype=dtype)
    model.device = torch.device(target)
    model.dtype = dtype
    if target == "cpu" and torch.cuda.is_available():
        torch.cuda.empty_cache()


def move_forced_aligner_to_device(aligner: Qwen3ForcedAligner, target_device: str) -> None:
    target = "cuda:0" if target_device == "gpu" and torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if target.startswith("cuda") else torch.float32
    aligner.model.to(device=target, dtype=dtype)
    aligner.device = torch.device(target)
    if target == "cpu" and torch.cuda.is_available():
        torch.cuda.empty_cache()


def run() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QWidget {
            background-color: #f4f1ea;
            color: #1f1a17;
            font-family: "Yu Gothic UI", "Segoe UI", sans-serif;
            font-size: 14px;
        }
        #heroPanel {
            background-color: #fffaf2;
            border: 1px solid #d8cab8;
            border-radius: 16px;
        }
        #dropArea {
            background-color: #f8efe2;
            border: 2px dashed #c4ab8c;
            border-radius: 14px;
        }
        #dropArea[dragActive="true"] {
            background-color: #efe0ca;
            border-color: #8f6a3f;
        }
        #dropHeading {
            font-size: 20px;
            font-weight: 600;
        }
        #dropDetail {
            color: #6e6258;
        }
        #fileMetaLabel, #timeLabel {
            color: #5e524a;
        }
        #segmentInfoLabel {
            color: #5e524a;
        }
        #segmentList, #resultText {
            background-color: #fbf6ef;
            border: 1px solid #d8cab8;
            border-radius: 10px;
            padding: 6px;
        }
        QPushButton {
            min-height: 36px;
            padding: 0 16px;
            border: 1px solid #b59b7b;
            border-radius: 10px;
            background-color: #ead9bf;
        }
        QPushButton:hover { background-color: #e4cfaf; }
        QPushButton:pressed { background-color: #d9bf97; }
        QPushButton:disabled { color: #7d736b; background-color: #e7e0d7; border-color: #d2c8bc; }
        QSlider::groove:horizontal {
            height: 6px;
            border-radius: 3px;
            background: #d7c7b5;
        }
        QSlider::handle:horizontal {
            width: 18px;
            margin: -6px 0;
            border-radius: 9px;
            background: #8f6a3f;
        }
        QProgressBar {
            min-height: 20px;
            border: 1px solid #d8cab8;
            border-radius: 10px;
            background-color: #fbf6ef;
            text-align: center;
        }
        QProgressBar::chunk {
            border-radius: 9px;
            background-color: #8f6a3f;
        }
        QStatusBar {
            background-color: #efe7db;
        }
        """
    )
    app.setLayoutDirection(Qt.LeftToRight)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
