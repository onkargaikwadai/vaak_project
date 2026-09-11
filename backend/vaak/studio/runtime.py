from __future__ import annotations

from dataclasses import dataclass

from ..envelope import ChannelClass, Register
from ..product.principals import PrincipalProfile
from ..voice_model import VoiceConditioning, VoiceIdentityClass
from .contracts import DirectorIntent, RightsScope, StudioCharacter, StudioError, StudioRightsGrant, StudioRightsService, VoiceOrigin


@dataclass(frozen=True)
class StudioSessionRequest:
    grant_id: str
    production_id: str
    usage_class: str = "dialogue"
    language: str = "en"
    territory: str = "worldwide"
    channel: ChannelClass = ChannelClass.LOCAL
    register: Register = Register.STAGE
    director: DirectorIntent = DirectorIntent()
    presence_evidence: str | None = None


class StudioRuntime:
    """First-class Vaak Studio character and rights workflow."""

    def __init__(self, product_runtime, rights: StudioRightsService | None = None, *, identity_verifier=None, require_identity_evidence: bool = False):
        self.product = product_runtime
        self.rights = rights or StudioRightsService()
        self.identity_verifier = identity_verifier
        self.require_identity_evidence = bool(require_identity_evidence)
        self.characters: dict[str, StudioCharacter] = {}
        self._restore_state()

    def _restore_state(self) -> None:
        state = getattr(self.product, "state", None)
        if not state:
            return
        for row in state.list_studio_characters():
            obj = dict(row["character"])
            obj["voice_origin"] = VoiceOrigin(obj.get("voice_origin", VoiceOrigin.SYNTHETIC.value))
            character = StudioCharacter(**obj)
            self.characters[character.character_id] = character
            holder = self.rights.rights_holder_key(self.product.get_principal(character.character_id).controller_id)
            self.product.core.owners[character.character_id] = holder
        for row in state.list_studio_rights():
            obj = dict(row["grant"])
            obj.pop("type", None)
            obj["scope"] = RightsScope(**{
                **obj["scope"],
                "production_ids": tuple(obj["scope"].get("production_ids", [])),
                "languages": tuple(obj["scope"].get("languages", ["en"])),
                "territories": tuple(obj["scope"].get("territories", ["worldwide"])),
                "channels": tuple(obj["scope"].get("channels", ["local", "call", "stream"])),
                "usage_classes": tuple(obj["scope"].get("usage_classes", ["dialogue"])),
            })
            self.rights.restore(StudioRightsGrant(**obj))

    def create_character(self, character: StudioCharacter, *, rights_holder_id: str, tenant_id: str = "default", performer_identity_evidence: str | None = None) -> StudioCharacter:
        if character.character_id in self.characters:
            raise StudioError("character already exists")
        if character.performer_id:
            if self.require_identity_evidence and not performer_identity_evidence:
                raise StudioError("licensed performer identity evidence required")
            if performer_identity_evidence:
                if self.identity_verifier is None:
                    raise StudioError("performer identity verifier not configured")
                ev = self.identity_verifier.verify(
                    performer_identity_evidence, tenant_id=tenant_id, subject_id=character.performer_id,
                    role="performer", purpose=f"studio_character:{character.character_id}",
                )
                from dataclasses import replace
                character = replace(character, performer_evidence_hash=ev.evidence_hash())
        self.characters[character.character_id] = character
        holder = self.rights.rights_holder_key(rights_holder_id)
        self.product.core.owners[character.character_id] = holder
        self.product.register_principal(PrincipalProfile(
            principal_id=character.character_id,
            controller_id=rights_holder_id,
            display_name=character.display_name,
            tenant_id=tenant_id,
            identity_class=VoiceIdentityClass.STUDIO_CHARACTER,
            persona=character.persona | {"studio_id": character.studio_id},
        ))
        if self.product.state:
            self.product.state.put_studio_character(tenant_id, character.character_id, character.body())
            self.product.state.event(tenant_id, "studio_character", character.character_id, "studio.character.created", character.body())
        return character

    def issue_rights(self, *, rights_holder_id: str, character_id: str, scope: RightsScope, ttl_seconds: float, rights_holder_identity_evidence: str | None = None) -> StudioRightsGrant:
        character = self.characters.get(character_id)
        if character is None:
            raise StudioError("unknown studio character")
        tenant_id = self.product.get_principal(character_id).tenant_id
        holder_evidence_hash = None
        if self.require_identity_evidence and not rights_holder_identity_evidence:
            raise StudioError("rights-holder identity evidence required")
        if rights_holder_identity_evidence:
            if self.identity_verifier is None:
                raise StudioError("rights-holder identity verifier not configured")
            ev = self.identity_verifier.verify(
                rights_holder_identity_evidence, tenant_id=tenant_id, subject_id=rights_holder_id,
                role="rights_holder", purpose=f"studio_rights:{character_id}",
            )
            holder_evidence_hash = ev.evidence_hash()
        grant = self.rights.issue(
            rights_holder_id=rights_holder_id,
            studio_id=character.studio_id,
            character=character,
            scope=scope,
            ttl_seconds=ttl_seconds,
            rights_holder_evidence_hash=holder_evidence_hash,
            performer_evidence_hash=character.performer_evidence_hash,
        )
        if self.product.state:
            tenant_id = self.product.get_principal(character_id).tenant_id
            self.product.state.put_studio_rights(tenant_id, grant.grant_id, grant.body())
            self.product.state.event(tenant_id, "studio_rights", grant.grant_id, "studio.rights.issued", grant.body())
        return grant

    def revoke_rights(self, grant_id: str, reason: str) -> StudioRightsGrant:
        grant = self.rights.revoke(grant_id, reason)
        if self.product.state:
            tenant_id = self.product.get_principal(grant.character_id).tenant_id
            self.product.state.put_studio_rights(tenant_id, grant.grant_id, grant.body())
            self.product.state.event(tenant_id, "studio_rights", grant.grant_id, "studio.rights.revoked", grant.body())
        for sid, live in list(self.product.live.items()):
            ctx = getattr(self.product.core.sessions.get(sid), "identity_context", {}) or {}
            if ctx.get("rights_grant", {}).get("grant_id") == grant_id:
                session = self.product.core.sessions.get(sid)
                if session:
                    session.terminate("studio_rights_revoked")
                    self.product.core.sessions.pop(sid, None)
                self.product.live.pop(sid, None)
        return grant

    def conditioning(self, character: StudioCharacter, grant: StudioRightsGrant, req: StudioSessionRequest) -> VoiceConditioning:
        return VoiceConditioning(
            principal_id=character.character_id,
            identity_class=VoiceIdentityClass.STUDIO_CHARACTER,
            persona=character.persona,
            register=req.register.value,
            authority={"rights_holder_id": grant.rights_holder_id, "grant_id": grant.grant_id},
            channel=req.channel.value,
            locale=req.language,
            director=req.director.body(),
            production={
                "studio_id": character.studio_id,
                "production_id": req.production_id,
                "usage_class": req.usage_class,
                "territory": req.territory,
            },
            performance_version=character.canonical_voice_version,
            rights_grant_hash=grant.grant_hash(),
        )

    def open_session(self, character_id: str, req: StudioSessionRequest):
        character = self.characters.get(character_id)
        if character is None:
            raise StudioError("unknown studio character")
        grant = self.rights.get(req.grant_id)
        if grant.character_id != character_id:
            raise StudioError("rights grant does not cover character")
        grant.authorize(
            production_id=req.production_id,
            language=req.language,
            territory=req.territory,
            channel=req.channel.value,
            usage_class=req.usage_class,
        )
        condition = self.conditioning(character, grant, req)
        identity_context = {
            "identity_class": "studio_character",
            "principal_id": character.character_id,
            "studio_character": character.body(),
            "rights_grant": grant.body(),
            "rights_grant_hash": grant.grant_hash(),
            "production": condition.production,
            "director": req.director.body(),
            "performance_version": character.canonical_voice_version,
            "conditioning": condition.body(),
            "conditioning_hash": condition.conditioning_hash(),
        }
        profile = self.product.get_principal(character_id)
        return self.product.open_live(
            character_id,
            mode="studio",
            channel=req.channel,
            register=req.register,
            language=req.language,
            tenant_id=profile.tenant_id,
            presence_evidence=req.presence_evidence,
            identity_context=identity_context,
        )
