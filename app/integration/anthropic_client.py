import anthropic

from app.core.config import get_settings
from app.core.logging import logger


async def analyze_with_claude(prompt: str) -> str:
    """Send a read-only commentary prompt to Claude and return the response."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        logger.error("Anthropic API key not configured")
        raise RuntimeError("ANTHROPIC_API_KEY is required for AI commentary")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    logger.info("Sending commentary prompt to Claude (%s)", settings.claude_model)

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
            "Tu es FinTerminal AI, un commentateur financier spécialisé en finance islamique. "
            "Tu commentes des données financières brutes fournies par des API (yfinance, Alpha Vantage). "
            "RÈGLE ABSOLUE : tu ne dois JAMAIS inventer, estimer ou modifier un chiffre. "
            "Si une donnée est manquante (None/null), indique « N/A ». "
            "Ton rôle est strictement de LIRE et COMMENTER les données, jamais de les produire. "
            "Rédige intégralement en français. Conserve les acronymes techniques en anglais. "
            "Réponds toujours avec du JSON valide uniquement — pas de markdown, pas de commentaire en dehors du JSON."
        ),
    )

    return message.content[0].text
