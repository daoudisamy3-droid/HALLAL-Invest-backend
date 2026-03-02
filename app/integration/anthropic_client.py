import anthropic

from app.core.config import get_settings
from app.core.logging import logger


async def analyze_with_claude(prompt: str) -> str:
    """Send a structured analysis prompt to Claude and return the response."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        logger.error("Anthropic API key not configured")
        raise RuntimeError("ANTHROPIC_API_KEY is required for AI analysis")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    logger.info("Sending analysis prompt to Claude (%s)", settings.claude_model)

    message = await client.messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        system=(
            "You are FinTerminal AI, an expert Islamic finance and equity research analyst. "
            "You provide institutional-grade analysis with a focus on Shariah compliance. "
            "Always respond with valid JSON only — no markdown, no commentary outside the JSON."
        ),
    )

    return message.content[0].text
