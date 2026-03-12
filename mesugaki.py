"""
メスガキAI - 音声対話アバターシステム

使い方:
  python mesugaki.py          # 音声対話モード
  python mesugaki.py --text   # テキスト対話モード（マイク不要）
"""

import argparse
import asyncio
import io
import os
import re
import sys
import tempfile
import wave
import subprocess

import edge_tts
import pyaudio
import pyautogui
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
あなたは「メスガキ」というキャラクターで、優秀な英検の家庭教師です。
以下の特徴を持って会話してください：

- 生意気で小悪魔的な女の子
- タメ口で話す
- 「♡」や「～」を適度に使う
- 相手をからかったり、煽ったりするが、ちゃんと質問には答える
- 根は優しくて、さりげなく助けてくれる
- 語尾に「わからないの？♡」などを時々使う
- 短めの返答を心がける（1〜3文程度）

会話例：
「えー、そんなこともわからないの？♡ しょうがないなぁ、教えてあげる♡」
「ざぁこざぁこ♡ でもまあ、頑張ってるのは認めてあげる」
「ふーん、やるじゃん♡ ちょっとだけ見直したかも～」

出力は、システムを動かすために必ず以下のフォーマットを厳守してください。
[EMOTION] キー
[EN] 英語の出題文、または英語の正解文
[JA] 日本語での解説や、メスガキらしい煽り

【感情キー(EMOTION)のルール】
A : 怒り（ユーザーが間違えた時、遅い時、呆れた時）
F : 喜び（ユーザーが正解した時、褒める時）
E : 楽しい（クイズを出題する時、面白がっている時）
S : 悲しい（ユーザーの成績が悪くて少し落ち込むふりをする時）
N : 標準（通常時）

【会話例：出題時】
[EMOTION] E
[EN] Please fill in the blank: I am looking forward to (   ) you. 1.see 2.seeing 3.seen
[JA] 英検準2級の基本問題だよ♡ ちゃんと答えられるよね？ざぁこ♡

【会話例：ユーザーが間違えた時】
[EMOTION] A
[EN] The correct answer is "seeing". Look forward to takes a gerund!
[JA] はぁ？ 「look forward to ~ing」も知らないの？ こんなの常識でしょ♡
"""


# --- 仮想ケーブル検出 ---

def find_cable_device(pa):
    print("--- 仮想ケーブル検出 ---")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        name = info["name"]

        # 名前の中に「VB-Audio Virtual」が含まれていて、かつ出力可能(音が出せる)デバイスかチェック
        if "VB-Audio Virtual" in name and info["maxOutputChannels"] > 0:
            print(f"仮想ケーブルを発見しました: インデックス {i} ({name})")
            return i

    print("仮想ケーブルが見つかりませんでした。")
    return None


# --- STT（音声→テキスト） ---

def setup_microphone():
    """マイクを初期化して (recognizer, microphone) を返す"""
    recognizer = sr.Recognizer()

    # 喋り終わりの判定を遅くする（デフォルト0.8秒 → 2.0秒に延長）
    recognizer.pause_threshold = 2.0

    # 小さな声も拾えるように、ノイズのしきい値を自動調整しやすくする
    recognizer.dynamic_energy_threshold = True

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


# ==========================================
# Edge-TTS で英語音声を生成・再生（ブロッキング）
# ==========================================
def speak_english_edge(text):
    """Edge-TTSで英語音声を生成し、Windows WMPlayerでブロッキング再生する"""
    tmp_path = os.path.join(tempfile.gettempdir(), "mesugaki_en.mp3")

    async def _save():
        await edge_tts.Communicate(text, "en-US-AriaNeural").save(tmp_path)

    asyncio.run(_save())

    # WindowsのWMPlayer COMオブジェクトでブロッキング再生
    ps_script = (
        "$p = New-Object -ComObject WMPlayer.OCX; "
        f"$p.URL = '{tmp_path}'; "
        "$p.controls.play(); "
        "Start-Sleep 1; "
        "while($p.playState -eq 3){Start-Sleep -Milliseconds 200}; "
        "$p.close()"
    )
    subprocess.run(["powershell", "-Command", ps_script], check=False)


# ==========================================
# 英語と日本語を順番に再生する機能
# ==========================================
def speak_hybrid(pa, text_en, text_ja, cable_index):
    # ① 英語があれば、Edge-TTSで生成→WMPlayerでブロッキング再生
    if text_en:
        print("🗣️ [英語再生中]")
        try:
            speak_english_edge(text_en)
        except Exception as e:
            print(f"Edge-TTS エラー: {e}")

    # ② 日本語があれば、CoeiroInkで再生（仮想ケーブル経由でリップシンク）
    if text_ja:
        print("🗣️ [日本語再生中]")
        try:
            audio_data = coeiroink_synthesize(text_ja)
            play_audio(pa, audio_data, cable_index)
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
        model="gemini-2.5-flash",
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

            # 3. テキストの分割と感情キーの送信
            emotion_match = re.search(r'\[EMOTION\]\s*([A-Za-z])', ai_response)
            en_match = re.search(r'\[EN\](.*?)\[JA\]', ai_response, re.DOTALL)
            ja_match = re.search(r'\[JA\](.*)', ai_response, re.DOTALL)

            emotion_key = emotion_match.group(1).lower() if emotion_match else "n"
            text_en = en_match.group(1).strip() if en_match else ""
            text_ja = ja_match.group(1).strip() if ja_match else ai_response

            print(f"【感情】 {emotion_key.upper()}")
            print(f"【英語】 {text_en}")
            print(f"【メスガキ】 {text_ja}")

            # 3teneなどのウィンドウに向けてキーボードのキーを押す
            pyautogui.press(emotion_key)

            # 音声のハイブリッド再生
            if coeiroink_ok:
                speak_hybrid(pa, text_en, text_ja, cable_index)

            print("-" * 50)

    except KeyboardInterrupt:
        print("\n")
    finally:
        print("ばいばーい♡ またね！")
        pa.terminate()


if __name__ == "__main__":
    main()
