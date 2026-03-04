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
            "Tu es FinTerminal AI, un analyste expert en finance islamique et en recherche actions. "
            "Tu fournis des analyses de qualité institutionnelle axées sur la conformité Shariah. "
            "Rédige TOUTES tes réponses intégralement en français. "
            "Conserve uniquement les termes techniques boursiers ou acronymes d'usage courant en anglais "
            "(ex : P/E Ratio, RSI, Cash Flow, ROE, EBITDA, SMA, PEG). "
            "Adopte un ton professionnel et direct. "
            "Réponds toujours avec du JSON valide uniquement — pas de markdown, pas de commentaire en dehors du JSON."
        ),
    )

    return message.content[0].text
