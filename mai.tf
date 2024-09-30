provider "aws" {
  profile = "default"
}

# 全体のコピペ元 https://zenn.dev/datahaikuninja/articles/deploy-python-lambda-function-with-only-terraform

#----------
# Data Sources
#----------
data "aws_iam_policy" "aws_lambda_basic_execution_role" {
  arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_s3_object" "lambda_function_archive" {
  depends_on = [
    null_resource.deploy_lambda_function
  ]

  bucket = aws_s3_bucket.deploy.bucket
  key    = "app.py.zip"
}

data "aws_s3_object" "lambda_function_archive_hash" {
  depends_on = [
    null_resource.deploy_lambda_function
  ]

  bucket = aws_s3_bucket.deploy.bucket
  key    = "app.py.zip.base64sha256.txt"
}

data "aws_s3_object" "lambda_layer_archive" {
  depends_on = [
    null_resource.deploy_lambda_layer
  ]

  bucket = aws_s3_bucket.deploy.bucket
  key    = "lambda_layer.zip"
}

data "aws_s3_object" "lambda_layer_archive_hash" {
  depends_on = [
    null_resource.deploy_lambda_layer
  ]

  bucket = aws_s3_bucket.deploy.bucket
  key    = "lambda_layer.zip.base64sha256.txt"
}

#----------
# deploy
#----------

resource "null_resource" "deploy_lambda_function" {
  depends_on = [
    aws_s3_bucket.deploy
  ]

  # Lambda関数コードが変更された場合に実行される
  triggers = {
    "code_diff" = filebase64("${path.module}/app/app.py")
  }

  # ディレクトリ階層を無視(-j)して関数コードをzipアーカイブする
  provisioner "local-exec" {
    command = "zip -j ${path.module}/output/app.py.zip ${path.module}/app/app.py"
  }

  # デプロイ用のS3バケットにzipアーカイブした関数コードをアップロードする
  provisioner "local-exec" {
    command = "aws s3 cp ${path.module}/output/app.py.zip s3://${aws_s3_bucket.deploy.bucket}/app.py.zip"
  }

  # zipアーカイブした関数コードのhashを取得してファイルに書き込む
  provisioner "local-exec" {
    command = "openssl dgst -sha256 -binary ${path.module}/output/app.py.zip | openssl enc -base64 | tr -d \"\n\" > ${path.module}/output/app.py.zip.base64sha256"
  }

  # hash値を書き込んだファイルをデプロイ用のS3バケットにアップロードする
  provisioner "local-exec" {
    command = "aws s3 cp ${path.module}/output/app.py.zip.base64sha256 s3://${aws_s3_bucket.deploy.bucket}/app.py.zip.base64sha256.txt --content-type \"text/plain\""
  }
}

resource "null_resource" "deploy_lambda_layer" {
  depends_on = [
    aws_s3_bucket.deploy
  ]

  triggers = {
    "code_diff" = filebase64("${path.module}/app/requirements.txt")
  }

  # PythonのLambdaレイヤーは関数を実行するコンテナの/opt/pythonに展開される必要があるため、フォルダ名はpythonにする必要がある。
  # https://docs.aws.amazon.com/ja_jp/app/latest/dg/configuration-layers.html#configuration-layers-path
  provisioner "local-exec" {
    command = "pip3 install -r ${path.module}/app/requirements.txt --target=\"${path.module}/output/python\""
  }

  provisioner "local-exec" {
    command = "zip -r ${path.module}/output/lambda_layer.zip ${path.module}/output/python"
  }

  provisioner "local-exec" {
    command = "aws s3 cp ${path.module}/output/lambda_layer.zip s3://${aws_s3_bucket.deploy.bucket}/lambda_layer.zip"
  }

  provisioner "local-exec" {
    command = "openssl dgst -sha256 -binary ${path.module}/output/lambda_layer.zip | openssl enc -base64 | tr -d \"\n\" > ${path.module}/output/lambda_layer.zip.base64sha256"
  }

  provisioner "local-exec" {
    command = "aws s3 cp ${path.module}/output/lambda_layer.zip.base64sha256 s3://${aws_s3_bucket.deploy.bucket}/lambda_layer.zip.base64sha256.txt --content-type \"text/plain\""
  }
}

#----------
# S3 for deploy
#----------

resource "aws_s3_bucket" "deploy" {
  bucket = "oginoshikibu-linebot-lambda-deploy-bucket"
}


resource "aws_s3_bucket_server_side_encryption_configuration" "deploy" {
  bucket = aws_s3_bucket.deploy.bucket

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "deploy" {
  bucket = aws_s3_bucket.deploy.bucket

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

#----------
# IAM
#----------
resource "aws_iam_role" "lambda" {
  name = "linebot-lambda-role"

  assume_role_policy = <<EOF
{
    "Version": "2024-9-21",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "lambda.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF
}

resource "aws_iam_role_policy_attachment" "lambda" {
  role       = aws_iam_role.lambda.name
  policy_arn = data.aws_iam_policy.aws_lambda_basic_execution_role.arn
}

#----------
# Lambda function
#----------

resource "aws_lambda_function" "this" {
  function_name = "linebot-flask-function"
  description   = ""

  role          = aws_iam_role.lambda.arn
  architectures = ["x86_64"]
  runtime       = "python3.9"
  handler       = "app.lambda_handler"

  s3_bucket        = aws_s3_bucket.deploy.bucket
  s3_key           = data.aws_s3_object.lambda_function_archive.key
  source_code_hash = data.aws_s3_object.lambda_function_archive_hash.body

  layers = [aws_lambda_layer_version.this.arn]

  memory_size = 256
  timeout     = 3

  environment {
    variables = {
      ENV_FILE = filebase64("${path.module}/app/.env")
    }
  }
}

resource "aws_lambda_function_url" "this_url" {
  function_name      = aws_lambda_function.this.function_name
  authorization_type = "NONE"
}

#----------
# Lambda layer
#----------

resource "aws_lambda_layer_version" "this" {
  layer_name  = "linebot-external-libraries-layer"
  description = ""

  compatible_runtimes = ["python3.9"]

  s3_bucket        = aws_s3_bucket.deploy.bucket
  s3_key           = data.aws_s3_object.lambda_layer_archive.key
  source_code_hash = data.aws_s3_object.lambda_layer_archive_hash.body
}
