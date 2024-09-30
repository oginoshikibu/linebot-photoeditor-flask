import base64
import os
import requests
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

GYAZO_ACCESS_TOKEN = os.environ["GYAZO_ACCESS_TOKEN"]

# Gyazo API


def get_images_list() -> requests.Response:
    url = f"https://api.gyazo.com/api/images?access_token={GYAZO_ACCESS_TOKEN}"
    response = requests.request("GET", url)
    return response

def get_image(image_url: str) -> Image:
    response = requests.request("GET", image_url)
    image = Image.open(io.BytesIO(response.content))
    return image

def delete_image(image_id: str) -> requests.Response:
    url = f"https://api.gyazo.com/api/images/{image_id}?access_token={GYAZO_ACCESS_TOKEN}"
    response = requests.request("DELETE", url)
    return response

def upload_image(image: Image) -> requests.Response:
    url = f"https://upload.gyazo.com/api/upload"
    image_byte_array = io.BytesIO()
    image.save(image_byte_array, format="PNG")
    image_byte_array.seek(0)
    files = {
        'access_token': GYAZO_ACCESS_TOKEN,
        'imagedata': image_byte_array
    }
    response = requests.request("POST", url, files=files)
    return response


app = Flask(__name__)
if not IS_AWS_LAMBDA:
    app.logger.setLevel(logging.INFO)
    IMAGE_SAVE_DIR = os.environ["IMAGE_SAVE_DIR"]

CHANNEL_ACCESS_TOKEN = os.environ["CHANNEL_ACCESS_TOKEN"]
CHANNEL_SECRET = os.environ["CHANNEL_SECRET"]
AUTH_USER_ID = os.environ["AUTH_USER_ID"]

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

    # specific user id
    try:
        user_id = request.json['events'][0]['source']['userId']
    except (KeyError, IndexError):
        # by webhook verification
        return 'OK'

    app.logger.info(f"user_id: {user_id}")
    if user_id != AUTH_USER_ID:
        line_bot_api.reply_message(
            request.json['events'][0]['replyToken'],
            TextSendMessage(text="This line bot is only for specific user, sorry. Please ask admin.")
        )
        return 'OK'

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    response = get_images_list()
    txt = response.text
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=txt))


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    os.mkdir(IMAGE_SAVE_DIR, exist_ok=True)

    if os.path.exists(IMAGE_SAVE_DIR, "merged.png"):
        os.remove(os.path.join(IMAGE_SAVE_DIR, "merged.png"))

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
    images = [Image.open(os.path.join(IMAGE_SAVE_DIR, f)) for f in os.listdir(IMAGE_SAVE_DIR) if f != "merged.png"]
    for f in os.listdir(IMAGE_SAVE_DIR):
        os.remove(os.path.join(IMAGE_SAVE_DIR, f))

    # 1枚の1080x1080にまとめる
    ON_A_SIDE = 1080
    SECTION_HEIGHT = ON_A_SIDE//len(images)
    new_image = Image.new('RGB', (ON_A_SIDE, ON_A_SIDE))
    cur_height = 0

    for i, image in enumerate(images):
        aim_height = SECTION_HEIGHT if i != len(images) - 1 else 1080 - cur_height

        image = image.resize((ON_A_SIDE, int(image.height * (ON_A_SIDE / image.width))))
        image = image.crop((0, (image.height - aim_height) // 2, ON_A_SIDE, (image.height + aim_height) // 2))

        new_image.paste(image, (0, cur_height))
        cur_height += image.height

    assert new_image.size == (ON_A_SIDE, ON_A_SIDE)
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
