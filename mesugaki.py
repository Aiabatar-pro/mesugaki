"""
メスガキAI - 音声対話アバターシステム

使い方:
  python mesugaki.py          # 音声対話モード
  python mesugaki.py --text   # テキスト対話モード（マイク不要）
"""

import argparse
import io
import os
import sys
import wave

import pyaudio
import requests
import speech_recognition as sr
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# 設定
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
COEIROINK_HOST = os.getenv("COEIROINK_HOST", "http://localhost:50032")
COEIROINK_SPEAKER_UUID = os.getenv(
    "COEIROINK_SPEAKER_UUID", "cb11bdbd-78fc-4f16-b528-a400bae1782d"
)
COEIROINK_STYLE_ID = int(os.getenv("COEIROINK_STYLE_ID", "92"))
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ja-JP")
VIRTUAL_CABLE_NAME = os.getenv("VIRTUAL_CABLE_NAME", "CABLE Input")

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


# --- 仮想ケーブル検出 ---

def find_cable_device(pa):
    """CABLE Inputのデバイス番号を探す。見つからなければNone"""
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if VIRTUAL_CABLE_NAME.lower() in info["name"].lower() and info["maxOutputChannels"] > 0:
            print(f"🔌 仮想ケーブル検出: [{i}] {info['name']}")
            return i
    print(f"⚠️  '{VIRTUAL_CABLE_NAME}' が見つかりません。スピーカーのみで再生します。")
    return None


# --- STT（音声→テキスト） ---

def setup_microphone():
    """マイクを初期化して (recognizer, microphone) を返す"""
    recognizer = sr.Recognizer()
    mic = sr.Microphone()
    with mic as source:
        print("🎤 環境音を調整中...")
        recognizer.adjust_for_ambient_noise(source, duration=1)
    print("🎤 マイク準備完了！")
    return recognizer, mic


def listen(recognizer, mic):
    """マイクから音声を取得してテキストに変換する"""
    with mic as source:
        print("\n（話してください...）")
        try:
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=30)
            return recognizer.recognize_google(audio, language=STT_LANGUAGE)
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            print("（聞き取れませんでした）")
            return None
        except sr.RequestError as e:
            print(f"STTエラー: {e}")
            return None


# --- CoeiroInk（テキスト→音声） ---

def coeiroink_synthesize(text):
    """CoeiroInk v1 API でテキストからWAV音声データを生成する"""
    # 1. 韻律推定
    prosody = requests.post(
        f"{COEIROINK_HOST}/v1/estimate_prosody",
        json={"text": text},
        timeout=30,
    ).json()

    # 2. 音声合成
    resp = requests.post(
        f"{COEIROINK_HOST}/v1/synthesis",
        json={
            "speakerUuid": COEIROINK_SPEAKER_UUID,
            "styleId": COEIROINK_STYLE_ID,
            "text": text,
            "prosodyDetail": prosody["detail"],
            "speedScale": 1.0,
            "volumeScale": 1.0,
            "pitchScale": 0.0,
            "intonationScale": 1.0,
            "prePhonemeLength": 0.1,
            "postPhonemeLength": 0.5,
            "outputSamplingRate": 44100,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def play_audio(pa, audio_data, cable_index):
    """スピーカーとCABLE Inputの両方に音声を再生する"""
    wf_speaker = wave.open(io.BytesIO(audio_data), "rb")
    fmt = pa.get_format_from_width(wf_speaker.getsampwidth())
    ch = wf_speaker.getnchannels()
    rate = wf_speaker.getframerate()

    stream_speaker = pa.open(format=fmt, channels=ch, rate=rate, output=True)

    # 仮想ケーブルが見つかっていれば同時出力
    wf_cable = None
    stream_cable = None
    if cable_index is not None:
        wf_cable = wave.open(io.BytesIO(audio_data), "rb")
        stream_cable = pa.open(format=fmt, channels=ch, rate=rate,
                               output=True, output_device_index=cable_index)

    try:
        while True:
            data = wf_speaker.readframes(1024)
            if not data:
                break
            stream_speaker.write(data)
            if stream_cable is not None:
                stream_cable.write(wf_cable.readframes(1024))
    finally:
        stream_speaker.stop_stream()
        stream_speaker.close()
        wf_speaker.close()
        if stream_cable is not None:
            stream_cable.stop_stream()
            stream_cable.close()
        if wf_cable is not None:
            wf_cable.close()


def speak(pa, text, cable_index):
    """テキストを音声合成して再生する"""
    try:
        audio_data = coeiroink_synthesize(text)
        play_audio(pa, audio_data, cable_index)
    except requests.ConnectionError:
        print("⚠️  CoeiroInkに接続できません。")
    except Exception as e:
        print(f"CoeiroInk エラー: {e}")


# --- メイン ---

def main():
    parser = argparse.ArgumentParser(description="メスガキAI")
    parser.add_argument("--text", action="store_true", help="テキストモードで起動")
    args = parser.parse_args()
    text_mode = args.text

    if not GEMINI_API_KEY:
        print("エラー: GEMINI_API_KEY が設定されていません。")
        print("  cp .env.example .env して API キーを設定してください。")
        sys.exit(1)

    print("=" * 50)
    print("  メスガキAI 起動中...")
    print("=" * 50)

    # Gemini 初期化
    client = genai.Client(api_key=GEMINI_API_KEY)
    chat = client.chats.create(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    print("🤖 Gemini API 接続OK")

    # CoeiroInk 接続確認
    coeiroink_ok = False
    try:
        resp = requests.get(f"{COEIROINK_HOST}/v1/speakers", timeout=5)
        resp.raise_for_status()
        print("🔊 CoeiroInk 接続OK（リリンちゃん メスガキ）")
        coeiroink_ok = True
    except requests.ConnectionError:
        print("⚠️  CoeiroInkに接続できません。音声なしで続行します。")

    # PyAudio & 仮想ケーブル初期化
    pa = pyaudio.PyAudio()
    cable_index = find_cable_device(pa) if coeiroink_ok else None

    # マイク初期化（音声モードのみ）
    recognizer, mic = None, None
    if not text_mode:
        try:
            recognizer, mic = setup_microphone()
        except OSError as e:
            print(f"⚠️  マイクが見つかりません: {e}")
            print("テキストモードに切り替えます。")
            text_mode = True

    print("=" * 50)
    print("  準備完了！")
    print("=" * 50)

    mode_str = "テキストモード" if text_mode else "音声モード"
    print(f"\n💬 会話を開始します（{mode_str}）")
    print("   終了するには Ctrl+C（テキストモードでは 'quit' も可）\n")
    print("-" * 50)

    try:
        while True:
            # 1. ユーザー入力を取得
            if text_mode:
                try:
                    user_text = input("\nあなた: ").strip()
                except EOFError:
                    break
                if not user_text:
                    continue
                if user_text.lower() in ("quit", "exit", "終了"):
                    break
            else:
                user_text = listen(recognizer, mic)
                if user_text is None:
                    continue
                print(f"あなた: {user_text}")

            # 2. Gemini でAI応答を生成
            try:
                ai_response = chat.send_message(user_text).text
            except Exception as e:
                print(f"Gemini APIエラー: {e}")
                ai_response = "あれ、ちょっと調子悪いかも... もう一回言って？♡"
            print(f"メスガキ: {ai_response}")

            # 3. CoeiroInk で音声再生
            if coeiroink_ok:
                speak(pa, ai_response, cable_index)

            print("-" * 50)

    except KeyboardInterrupt:
        print("\n")
    finally:
        print("ばいばーい♡ またね！")
        pa.terminate()


if __name__ == "__main__":
    main()
