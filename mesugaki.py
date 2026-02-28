"""
メスガキAI - 3tene VRMアバター + VOICEVOX + STT + Gemini API 対話システム

使い方:
  python mesugaki.py          # 音声対話モード
  python mesugaki.py --text   # テキスト対話モード（マイク不要）
"""

import argparse
import io
import os
import sys
import wave

import requests
import speech_recognition as sr
from dotenv import load_dotenv
from google import genai
from google.genai import types

# .envファイルの読み込み
load_dotenv()

# ============================================================
# 設定
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VOICEVOX_HOST = os.getenv("VOICEVOX_HOST", "http://localhost:50021")
VOICEVOX_SPEAKER_ID = int(os.getenv("VOICEVOX_SPEAKER_ID", "0"))
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ja-JP")
VIRTUAL_CABLE_NAME = os.getenv("VIRTUAL_CABLE_NAME", "CABLE Input")

# メスガキのシステムプロンプト
SYSTEM_PROMPT = """\
あなたは「メスガキ」というキャラクターです。
以下の特徴を持って会話してください：

- 生意気で小悪魔的な女の子
- タメ口で話す
- 「♡」や「～」を適度に使う
- 相手をからかったり、煽ったりするが、ちゃんと質問には答える
- 根は優しくて、さりげなく助けてくれる
- 語尾に「ざぁこ♡」「わからないの？♡」などを時々使う
- 短めの返答を心がける（1〜3文程度）
- 日本語で話す

会話例：
「えー、そんなこともわからないの？♡ しょうがないなぁ、教えてあげる♡」
「ざぁこざぁこ♡ でもまあ、頑張ってるのは認めてあげる」
「ふーん、やるじゃん♡ ちょっとだけ見直したかも～」
"""


# ============================================================
# STT（Speech-to-Text）
# ============================================================


class STTHandler:
    """Google Speech Recognition を使った音声認識"""

    def __init__(self, language="ja-JP"):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.language = language

        # 環境音の調整
        with self.microphone as source:
            print("🎤 環境音を調整中...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
        print("🎤 マイク準備完了！")

    def listen(self):
        """マイクから音声を取得してテキストに変換する"""
        with self.microphone as source:
            print("\n（話してください...）")
            try:
                audio = self.recognizer.listen(
                    source, timeout=10, phrase_time_limit=30
                )
                text = self.recognizer.recognize_google(
                    audio, language=self.language
                )
                return text
            except sr.WaitTimeoutError:
                return None
            except sr.UnknownValueError:
                print("（聞き取れませんでした）")
                return None
            except sr.RequestError as e:
                print(f"STTエラー: {e}")
                return None


# ============================================================
# Gemini API
# ============================================================


class GeminiHandler:
    """Gemini API を使ったチャット（google-genai SDK）"""

    def __init__(self, api_key, system_prompt):
        self.client = genai.Client(api_key=api_key)
        self.chat = self.client.chats.create(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
            ),
        )

    def send_message(self, text):
        """メッセージを送信してAI応答を取得する"""
        try:
            response = self.chat.send_message(text)
            return response.text
        except Exception as e:
            print(f"Gemini APIエラー: {e}")
            return "あれ、ちょっと調子悪いかも... もう一回言って？♡"


# ============================================================
# VOICEVOX（Text-to-Speech）
# ============================================================


class VoicevoxHandler:
    """VOICEVOX を使った音声合成"""

    def __init__(self, host="http://localhost:50021", speaker_id=0,
                 virtual_cable_name="CABLE Input"):
        self.host = host
        self.speaker_id = speaker_id
        self.virtual_cable_name = virtual_cable_name
        # PyAudioは音声再生時にのみimportする
        self._pyaudio = None
        self._cable_device_index = None
        self._cable_searched = False

    @property
    def pyaudio_instance(self):
        if self._pyaudio is None:
            import pyaudio

            self._pyaudio = pyaudio.PyAudio()
        return self._pyaudio

    def find_cable_device(self):
        """VB-Audio Virtual Cable（CABLE Input）のデバイスインデックスを検索する"""
        if self._cable_searched:
            return self._cable_device_index

        self._cable_searched = True
        pa = self.pyaudio_instance
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info.get("name", "")
            if self.virtual_cable_name.lower() in name.lower() and info["maxOutputChannels"] > 0:
                self._cable_device_index = i
                print(f"🔌 仮想ケーブル検出: [{i}] {name}")
                return self._cable_device_index

        print(f"⚠️  仮想ケーブル '{self.virtual_cable_name}' が見つかりません。")
        print("   VB-Audio Virtual Cableがインストールされているか確認してください。")
        print("   スピーカーのみで再生します。")
        return None

    def check_connection(self):
        """VOICEVOXへの接続を確認する"""
        try:
            resp = requests.get(f"{self.host}/version", timeout=3)
            resp.raise_for_status()
            print(f"🔊 VOICEVOX 接続OK (version: {resp.text})")
            return True
        except requests.ConnectionError:
            print(
                "⚠️  VOICEVOXに接続できません。"
                "VOICEVOXを起動してから再実行してください。"
            )
            return False

    def synthesize(self, text):
        """テキストから音声データ(WAV)を生成する"""
        # 音声クエリの作成
        query_resp = requests.post(
            f"{self.host}/audio_query",
            params={"text": text, "speaker": self.speaker_id},
            timeout=30,
        )
        query_resp.raise_for_status()
        query = query_resp.json()

        # 音声合成
        synth_resp = requests.post(
            f"{self.host}/synthesis",
            params={"speaker": self.speaker_id},
            json=query,
            timeout=60,
        )
        synth_resp.raise_for_status()
        return synth_resp.content

    def _open_stream(self, pa, wf, device_index=None):
        """指定デバイスへの出力ストリームを開く"""
        kwargs = {
            "format": pa.get_format_from_width(wf.getsampwidth()),
            "channels": wf.getnchannels(),
            "rate": wf.getframerate(),
            "output": True,
        }
        if device_index is not None:
            kwargs["output_device_index"] = device_index
        return pa.open(**kwargs)

    def play_audio(self, audio_data):
        """WAV音声データをスピーカーと仮想ケーブルの両方に同時再生する"""
        pa = self.pyaudio_instance
        cable_index = self.find_cable_device()

        # スピーカー用ストリーム
        wf_speaker = wave.open(io.BytesIO(audio_data), "rb")
        stream_speaker = self._open_stream(pa, wf_speaker)

        # 仮想ケーブル用ストリーム（検出できた場合）
        wf_cable = None
        stream_cable = None
        if cable_index is not None:
            wf_cable = wave.open(io.BytesIO(audio_data), "rb")
            stream_cable = self._open_stream(pa, wf_cable, cable_index)

        try:
            chunk_size = 1024
            while True:
                data_speaker = wf_speaker.readframes(chunk_size)
                if not data_speaker:
                    break
                stream_speaker.write(data_speaker)

                if stream_cable is not None:
                    data_cable = wf_cable.readframes(chunk_size)
                    if data_cable:
                        stream_cable.write(data_cable)
        finally:
            stream_speaker.stop_stream()
            stream_speaker.close()
            wf_speaker.close()
            if stream_cable is not None:
                stream_cable.stop_stream()
                stream_cable.close()
            if wf_cable is not None:
                wf_cable.close()

    def speak(self, text):
        """テキストを音声合成して再生する"""
        try:
            audio_data = self.synthesize(text)
            self.play_audio(audio_data)
        except requests.ConnectionError:
            print("⚠️  VOICEVOXに接続できません。VOICEVOXが起動しているか確認してください。")
        except Exception as e:
            print(f"VOICEVOX エラー: {e}")

    def cleanup(self):
        """リソースを解放する"""
        if self._pyaudio is not None:
            self._pyaudio.terminate()


# ============================================================
# メインアプリケーション
# ============================================================


class MesugakiAI:
    """メスガキAI 対話システム"""

    def __init__(self, text_mode=False):
        if not GEMINI_API_KEY:
            print("エラー: GEMINI_API_KEY が設定されていません。")
            print(".env ファイルを作成して API キーを設定してください。")
            print("  cp .env.example .env")
            print("  # .env を編集して GEMINI_API_KEY を設定")
            sys.exit(1)

        self.text_mode = text_mode

        print("=" * 50)
        print("  メスガキAI 起動中...")
        print("=" * 50)

        # Gemini 初期化
        self.gemini = GeminiHandler(GEMINI_API_KEY, SYSTEM_PROMPT)
        print("🤖 Gemini API 接続OK")

        # VOICEVOX 初期化
        self.voicevox = VoicevoxHandler(
            VOICEVOX_HOST, VOICEVOX_SPEAKER_ID, VIRTUAL_CABLE_NAME
        )
        self.voicevox_available = self.voicevox.check_connection()
        if self.voicevox_available:
            self.voicevox.find_cable_device()

        # STT 初期化（音声モードのみ）
        self.stt = None
        if not text_mode:
            try:
                self.stt = STTHandler(language=STT_LANGUAGE)
            except OSError as e:
                print(f"⚠️  マイクが見つかりません: {e}")
                print("テキストモードに切り替えます。")
                self.text_mode = True

        print("=" * 50)
        print("  準備完了！")
        print("=" * 50)

    def get_user_input(self):
        """ユーザー入力を取得する（音声 or テキスト）"""
        if self.text_mode:
            try:
                text = input("\nあなた: ").strip()
                return text if text else None
            except EOFError:
                return "quit"
        else:
            return self.stt.listen()

    def run(self):
        """メイン対話ループ"""
        mode_str = "テキストモード" if self.text_mode else "音声モード"
        print(f"\n💬 会話を開始します（{mode_str}）")

        if not self.text_mode:
            print("   3teneでリップシンク（音声入力）を有効にしてください。")
            print("   VOICEVOXの音声出力を3teneが拾ってリップシンクします。")

        print("   終了するには Ctrl+C（テキストモードでは 'quit' も可）\n")
        print("-" * 50)

        try:
            while True:
                # 1. ユーザー入力を取得
                user_text = self.get_user_input()
                if user_text is None:
                    continue
                if user_text.lower() in ("quit", "exit", "終了"):
                    break

                if not self.text_mode:
                    print(f"あなた: {user_text}")

                # 2. Gemini でAI応答を生成
                ai_response = self.gemini.send_message(user_text)
                print(f"メスガキ: {ai_response}")

                # 3. VOICEVOX で音声再生
                if self.voicevox_available:
                    self.voicevox.speak(ai_response)

                print("-" * 50)

        except KeyboardInterrupt:
            print("\n")
        finally:
            print("ばいばーい♡ またね！")
            self.voicevox.cleanup()


# ============================================================
# エントリーポイント
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="メスガキAI - AI対話アバターシステム")
    parser.add_argument(
        "--text",
        action="store_true",
        help="テキストモードで起動（マイク不要）",
    )
    args = parser.parse_args()

    ai = MesugakiAI(text_mode=args.text)
    ai.run()


if __name__ == "__main__":
    main()
