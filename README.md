# メスガキAI

3tene VRMアバター + CoeiroInk + STT + Gemini API を連携させた対話AIアバターシステム。

## システム構成

```
マイク → STT(Google Speech Recognition) → テキスト
                                            ↓
                                      Gemini API (AI応答生成)
                                            ↓
                                      CoeiroInk (音声合成 - リリンちゃん メスガキ)
                                            ↓
                                      スピーカー再生 → 3tene (リップシンク)
```

## 必要なもの

- Python 3.9以上
- [CoeiroInk](https://coeiroink.com/) （ローカルで起動しておく）
- [3tene](https://3tene.com/) + 自作VRMファイル
- [Gemini API キー](https://aistudio.google.com/apikey)
- マイク（音声モードの場合）

## セットアップ

### 1. リポジトリのクローンと依存パッケージのインストール

```bash
git clone https://github.com/Aiabatar-pro/mesugaki.git
cd mesugaki
pip install -r requirements.txt
```

> **注意**: PyAudioのインストールにはPortAudioが必要です。
> - **Windows**: `pip install pyaudio` で自動インストールされます
> - **macOS**: `brew install portaudio && pip install pyaudio`
> - **Linux**: `sudo apt-get install portaudio19-dev && pip install pyaudio`

### 2. 環境変数の設定

```bash
cp .env.example .env
```

`.env` ファイルを編集して Gemini API キーを設定:

```
GEMINI_API_KEY=あなたのAPIキー
```

### 3. CoeiroInkの起動

CoeiroInkアプリケーションを起動してください（デフォルトで http://localhost:50032 で待機）。

### 4. 3teneの設定

1. 3teneを起動し、自作のVRMファイルを読み込む
2. **リップシンク設定** → 「音声入力」を選択
3. 音声入力デバイスをスピーカー出力（またはステレオミキサー/仮想オーディオデバイス）に設定
   - これにより、CoeiroInkの音声出力でアバターの口が動くようになります

> **ヒント**: VB-CABLEなどの仮想オーディオデバイスを使うと、スピーカーから音を出しつつ3teneにも音声を入力できます。

## 使い方

### 音声対話モード（マイクで話しかける）

```bash
python mesugaki.py
```

### テキスト対話モード（キーボード入力）

```bash
python mesugaki.py --text
```

## CoeiroInkのスピーカー変更

`.env` で `COEIROINK_SPEAKER_UUID` と `COEIROINK_STYLE_ID` を変更すると声を変えられます。

デフォルトは **リリンちゃん（メスガキ）** スタイルです。

全スピーカー一覧は CoeiroInk 起動中に http://localhost:50032/v1/speakers で確認できます。

## トラブルシューティング

| 症状 | 対処法 |
|------|--------|
| `GEMINI_API_KEY が設定されていません` | `.env` ファイルにAPIキーを設定してください |
| `CoeiroInkに接続できません` | CoeiroInkアプリを起動してください |
| `マイクが見つかりません` | マイクを接続するか `--text` モードを使用してください |
| `PyAudio インストールエラー` | PortAudioをインストールしてから再実行してください |
| 3teneの口が動かない | リップシンクの音声入力デバイス設定を確認してください |
