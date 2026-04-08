from openai import OpenAI

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="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"
)

response = client.chat.completions.create(
    model="openrouter/free",
    messages=[
        {"role": "user", "content": "Reply with exactly: OpenRouter test successful"}
    ]
)

print(response.choices[0].message.content)