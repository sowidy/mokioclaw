import os
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model


def create_model():
    """
    创建对话模型
    :return:
    """
    load_dotenv(override=True)

    api_key = os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL")
    model_name = os.getenv("DEEPSEEK_MODEL_NAME")

    missing = [name for name, value in {'model_name':model_name, 'api_key':api_key, 'base_url':base_url}.items() if value is None]

    if missing:
        raise Exception("Missing required environment variables: {}".format(missing))
    return init_chat_model(
        model=model_name,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
        temperature=0
    )

if __name__ == "__main__":
    model = create_model()
    response = model.invoke("介绍一下你自己")
    print(response.content)