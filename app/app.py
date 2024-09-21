import base64
from io import StringIO
import os
import dotenv
import awsgi
from flask import Flask, request, abort, send_file

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage, ImageSendMessage
)
from PIL import Image
import io

IS_AWS_LAMBDA = 'AWS_LAMBDA_FUNCTION_NAME' in os.environ

if IS_AWS_LAMBDA:
    # AWS Lambda環境(.envをterraformでENV_FILEにbase64エンコードして環境変数に設定済み)
    env_file_content = base64.b64decode(os.environ['ENV_FILE'])
    env_file_str = env_file_content.decode('utf-8')
    env_file = StringIO(env_file_str)
    dotenv.load_dotenv(stream=env_file)
else:
    # ローカル環境
    dotenv.load_dotenv('.env')

CHANNEL_ACCESS_TOKEN = os.environ["CHANNEL_ACCESS_TOKEN"]
CHANNEL_SECRET = os.environ["CHANNEL_SECRET"]


app = Flask(__name__)

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

if not IS_AWS_LAMBDA:
    app.run(host='0.0.0.0', port=5000, debug=True)

@app.route("/")
def hello_world():
    return "Hello World!"

@app.route("/image/<filename>", methods=["GET"])
def get_image(filename):
    return send_file("image/" + filename, mimetype='image/png')

@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=event.message.text))


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    message_id = event.message.id

    # message_idから画像のバイナリデータを取得
    message_content = line_bot_api.get_message_content(message_id)

    # バイナリデータをPIL.Imageに変換
    image = Image.open(io.BytesIO(message_content.content))

    # 半分に切る
    width, height = image.size
    image = image.crop((0, 0, width // 2, height))

    # save image
    image.save("image.jpg")


    # 返信
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text="画像を受け取りました。"),
            ImageSendMessage(
                original_content_url="https://3eb7-126-159-18-222.ngrok-free.app/image",
                preview_image_url="https://3eb7-126-159-18-222.ngrok-free.app/image"
            )

        ]
    )


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    message_id = event.message.id

    # message_idから画像のバイナリデータを取得
    message_content = line_bot_api.get_message_content(message_id)

    # バイナリデータをPIL.Imageに変換
    image = Image.open(io.BytesIO(message_content.content))

    # 半分に切る
    width, height = image.size
    image = image.crop((0, 0, width // 2, height))

    # save image
    image.save("image.jpg")


    # 返信
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text="画像を受け取りました。"),
            ImageSendMessage(
                original_content_url="https://3eb7-126-159-18-222.ngrok-free.app/image",
                preview_image_url="https://3eb7-126-159-18-222.ngrok-free.app/image"
            )

        ]
    )


def lambda_handler(event, context):
    # lambdaのURLsからのリクエストをFlaskのリクエストに変換
    # https://github.com/slank/awsgi/issues/73
    event['httpMethod'] = event['requestContext']['http']['method']
    event['path'] = event['requestContext']['http']['path']
    event['queryStringParameters'] = event.get('queryStringParameters', {})
    return awsgi.response(app, event, context)
