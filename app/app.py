import base64
import io
import json
import logging
import os

import awsgi
import dotenv
import requests
from flask import Flask, abort, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (ButtonsTemplate, ImageMessage, ImageSendMessage,
                            MessageEvent, PostbackAction, PostbackEvent,
                            TemplateSendMessage, TextMessage, TextSendMessage)
from PIL import Image

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
app.logger.setLevel(logging.INFO)

CHANNEL_ACCESS_TOKEN = os.environ["CHANNEL_ACCESS_TOKEN"]
CHANNEL_SECRET = os.environ["CHANNEL_SECRET"]
AUTH_USER_ID = os.environ["AUTH_USER_ID"]
GYAZO_ACCESS_TOKEN = os.environ["GYAZO_ACCESS_TOKEN"]

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


def get_images_list() -> requests.Response:
    url = f"https://api.gyazo.com/api/images?access_token={GYAZO_ACCESS_TOKEN}"
    response = requests.request("GET", url)
    app.logger.info(json.dumps(response.json(), indent=2))
    return response


def get_image(image_url: str) -> Image:
    response = requests.request("GET", image_url)
    image = Image.open(io.BytesIO(response.content))
    return image


def delete_image(image_id: str) -> requests.Response:
    url = f"https://api.gyazo.com/api/images/{image_id}?access_token={GYAZO_ACCESS_TOKEN}"
    response = requests.request("DELETE", url)
    app.logger.info(json.dumps(response.json(), indent=2))
    return response


def upload_image(image: Image) -> requests.Response:
    url = f"https://upload.gyazo.com/api/upload?access_token={GYAZO_ACCESS_TOKEN}"
    image_byte_array = io.BytesIO()
    image.save(image_byte_array, format="PNG")
    image_byte_array.seek(0)
    files = {
        'imagedata': image_byte_array
    }
    response = requests.request("POST", url, files=files)
    app.logger.info(json.dumps(response.json(), indent=2))
    return response


def delete_all_images():
    response = get_images_list()
    images = response.json()
    for image in images:
        delete_image(image["image_id"])
    return


def get_all_images() -> list[Image]:  # type: ignore
    response = get_images_list()
    images = response.json()
    images.sort(key=lambda x: x["created_at"])
    image_list = [get_image(image["url"]) for image in images]
    return image_list


@app.route("/")  # ヘルスチェック用
def hello_world():
    return "Hello World!"


@app.route("/callback", methods=['POST'])   # LINEからのリクエストを受け取るエンドポイント
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        user_id = request.json['events'][0]['source']['userId']
    except (KeyError, IndexError):  # developer consoleからのテスト用
        return 'OK'

    if user_id != AUTH_USER_ID:  # 認証ユーザー以外は返信しない
        line_bot_api.reply_message(
            request.json['events'][0]['replyToken'],
            TextSendMessage(text="This line bot is only for specific user, sorry. Please ask admin.")
        )
        return 'OK'

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        app.logger.info(e)
        line_bot_api.reply_message(
            request.json['events'][0]['replyToken'],
            TextSendMessage(text="Error occurred. Please ask admin.")
        )
        abort(500)
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)  # テキストメッセージ時、画像一覧を返信して削除するか確認
def handle_message(event):
    response = get_images_list()
    images = response.json()
    app.logger.info(json.dumps(images, indent=2))
    if len(images) == 0:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="画像がありません。")
        )
        return

    thumb_URLs = [image["thumb_url"] for image in images]
    main_URLs = [image["url"] for image in images]

    line_bot_api.reply_message(
        event.reply_token, [
            TextSendMessage(text=f"画像が{len(images)}枚保存されています。"),
        ]
        + [
            ImageSendMessage(
                original_content_url=main_URL,
                preview_image_url=thumb_URL
            ) for main_URL, thumb_URL in zip(main_URLs[:3], thumb_URLs[:3])  # 同時に送信できるmessage数は上限5枚のため
        ]
        + [
            TemplateSendMessage(
                alt_text='Buttons template',
                template=ButtonsTemplate(
                    text=f'{len(images)}枚の画像を削除しますか？',
                    actions=[
                        PostbackAction(
                            label='delete',
                            display_text='delete',
                            data='delete'
                        )
                    ]))
        ]
    )


@ handler.add(MessageEvent, message=ImageMessage)   # 画像メッセージ時、uploadして結合するか確認
def handle_image(event):
    # get image
    message_content = line_bot_api.get_message_content(event.message.id)
    image = Image.open(io.BytesIO(message_content.content))
    image_size = image.size
    upload_image(image)

    # 返信
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text=f"画像を受け取りました。{image_size=}, "),
            TemplateSendMessage(
                alt_text='Buttons template',
                template=ButtonsTemplate(
                    text='結合しますか？',
                    actions=[
                        PostbackAction(
                            label='merge',
                            display_text='merge',
                            data='merge'
                        )
                    ])),
        ]
    )


@ handler.add(PostbackEvent)    # ポストバック処理（delete, merge）
def handle_postback(event):
    if event.postback.data == 'delete':
        delete_all_images()
        images = get_images_list().json()
        if len(images) == 0:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="画像を削除しました。")
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"画像の削除に失敗しました。\n{len(images)}枚の画像が残っています。\n{json.dumps(images, indent=2)}")
            )
        return

    if event.postback.data == 'merge':
        response = edit_image().json()
        thumb_URL = response["thumb_url"]
        main_URL = response["url"]

        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text=f"画像を編集しました。"),
                ImageSendMessage(
                    original_content_url=main_URL,
                    preview_image_url=thumb_URL
                ),
            ]
        )


def edit_image() -> requests.Response:  # 画像を結合してアップロード
    images = get_all_images()

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
    res = upload_image(new_image)
    app.logger.info(json.dumps(res.json(), indent=2))
    return res


def lambda_handler(event, context):
    # lambdaのURLsからのリクエストをFlaskのリクエストに変換
    # https://github.com/slank/awsgi/issues/73
    event['httpMethod'] = event['requestContext']['http']['method']
    event['path'] = event['requestContext']['http']['path']
    event['queryStringParameters'] = event.get('queryStringParameters', {})
    return awsgi.response(app, event, context)


if not IS_AWS_LAMBDA:
    app.run(host='0.0.0.0', port=5000, debug=True)
