export async function askModel(prompt: string) {
  return fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
    body: JSON.stringify({ model: 'user-configured', messages: [{ role: 'user', content: prompt }] })
  });
}
