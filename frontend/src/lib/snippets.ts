/** Copy-pasteable client snippets for an application's endpoint. */

const KEY_PLACEHOLDER = "YOUR_API_KEY";

function keyFor(apiKey?: string | null): string {
  return apiKey || KEY_PLACEHOLDER;
}

export function curlSnippet(baseUrl: string, model: string, apiKey?: string | null): string {
  return `curl ${baseUrl}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${keyFor(apiKey)}" \\
  -d '{
    "model": "${model}",
    "messages": [
      { "role": "user", "content": "What does our policy say about leave?" }
    ]
  }'`;
}

export function pythonSnippet(baseUrl: string, model: string, apiKey?: string | null): string {
  return `# pip install openai
from openai import OpenAI

client = OpenAI(
    base_url="${baseUrl}/v1",
    api_key="${keyFor(apiKey)}",
)

response = client.chat.completions.create(
    model="${model}",
    messages=[
        {"role": "user", "content": "What does our policy say about leave?"},
    ],
)

print(response.choices[0].message.content)`;
}

export function typescriptSnippet(baseUrl: string, model: string, apiKey?: string | null): string {
  return `// npm install openai   (Node.js)
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "${baseUrl}/v1",
  apiKey: "${keyFor(apiKey)}",
});

const response = await client.chat.completions.create({
  model: "${model}",
  messages: [
    { role: "user", content: "What does our policy say about leave?" },
  ],
});

console.log(response.choices[0].message.content);`;
}
