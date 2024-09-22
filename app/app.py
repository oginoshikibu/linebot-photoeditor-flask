import base64
import os
import io
import logging
import dotenv
import awsgi
from PIL import Image
from flask import Flask, request, abort, send_file

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage, ImageSendMessage,
    ButtonsTemplate, TemplateSendMessage, PostbackAction, PostbackEvent
)

IS_AWS_LAMBDA = 'AWS_LAMBDA_FUNCTION_NAME' in os.environ

if IS_AWS_LAMBDA:
    # AWS Lambda環境(.envをterraformでENV_FILEにbase64エンコードして環境変数に設定済み)
    env_file_content = base64.b64decode(os.environ['ENV_FILE'])
    env_file_str = env_file_content.decode('utf-8')
    env_file = io.StringIO(env_file_str)
    dotenv.load_dotenv(stream=env_file)
else:
    # ローカル環境
    dotenv.load_dotenv('.env')

app = Flask(__name__)
if not IS_AWS_LAMBDA:
    app.logger.setLevel(logging.INFO)
    IMAGE_SAVE_DIR = os.environ["IMAGE_SAVE_DIR"]

CHANNEL_ACCESS_TOKEN = os.environ["CHANNEL_ACCESS_TOKEN"]
CHANNEL_SECRET = os.environ["CHANNEL_SECRET"]
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/")
def hello_world():
    return "Hello World!"


if not IS_AWS_LAMBDA:
    @app.route("/image/<filename>", methods=["GET"])
    def get_image(filename):
        image_path = os.path.join(IMAGE_SAVE_DIR, filename)
        if not os.path.exists(image_path):
            abort(404)
        return send_file(image_path, mimetype='image/png')


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

    # get image
    message_content = line_bot_api.get_message_content(message_id)
    image = Image.open(io.BytesIO(message_content.content))
    image_size = image.size

    # save image
    if not IS_AWS_LAMBDA:
        image.save(os.path.join(IMAGE_SAVE_DIR, f"{message_id}.png"))
        files_count = len(os.listdir(IMAGE_SAVE_DIR))

    # 返信
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text=f"画像を受け取りました。{image_size=}, {files_count=}"),
            TemplateSendMessage(
                alt_text='Buttons template',
                template=ButtonsTemplate(
                    text='結合しますか？',
                    actions=[
                        PostbackAction(
                            label='Yes',
                            display_text='Yes',
                            data='yes'
                        ),
                        PostbackAction(
                            label='No',
                            display_text='No',
                            data='no'
                        )
                    ]
                )
            ),

        ]
    )


@handler.add(PostbackEvent)
def handle_postback(event):
    if event.postback.data == 'no':
        return
    edit_image()
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text=f"画像を編集しました。{os.environ['API_URL']}/image/merged.png"),
            ImageSendMessage(
                original_content_url=f"{os.environ['API_URL']}/image/merged.png",
                preview_image_url=f"{os.environ['API_URL']}/image/merged.png",
            ),
        ]
    )


def edit_image():
    images = [Image.open(os.path.join(IMAGE_SAVE_DIR, f)) for f in os.listdir(IMAGE_SAVE_DIR)]
    # 1枚の1080x1080にまとめる
    width = 1080
    height = 1080/len(images)
    new_image = Image.new('RGB', (width, width))

    for i, image in enumerate(images):

        image = image.resize(
            (width, int(image.height * (width / image.width)))
        ).crop(
            (0, (image.height - height) / 2, width, (image.height + height) / 2)
        )

        new_image.paste(image, (0, int(i * height)))

    new_image.save(os.path.join(IMAGE_SAVE_DIR, "merged.png"))


def lambda_handler(event, context):
    # lambdaのURLsからのリクエストをFlaskのリクエストに変換
    # https://github.com/slank/awsgi/issues/73
    event['httpMethod'] = event['requestContext']['http']['method']
    event['path'] = event['requestContext']['http']['path']
    event['queryStringParameters'] = event.get('queryStringParameters', {})
    return awsgi.response(app, event, context)


if not IS_AWS_LAMBDA:
    app.run(host='0.0.0.0', port=5000, debug=True)
