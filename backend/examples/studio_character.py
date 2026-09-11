"""Illustrative Vaak Studio flow using the Python SDK."""
from vaak.client import VaakClient

client = VaakClient("https://vaak.example.com", "replace-me")
character = client.create_studio_character(
    "studio_orbit", rights_holder_id="rights_orbit", character_id="nova",
    display_name="Nova", persona={"style":"cinematic"}, voice_origin="synthetic",
    canonical_voice_version="season-2",
)
print(character)
