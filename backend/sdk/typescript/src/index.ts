export type RealtimeEvent = Record<string, unknown> & { type: string };

export class VaakClient {
  constructor(public baseUrl: string, public apiKey: string) {}
  private headers() { return {"Authorization": `Bearer ${this.apiKey}`, "Content-Type":"application/json"}; }

  async createPrincipal(input: {controller_id:string; display_name:string; identity_class?:string; persona?:Record<string,unknown>; principal_id?:string}) {
    const r = await fetch(`${this.baseUrl}/v1/principals`, {method:"POST", headers:this.headers(), body:JSON.stringify(input)});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async openVoiceEnrollment(principalId:string) {
    const r = await fetch(`${this.baseUrl}/v1/principals/${principalId}/voice/enrollment`, {method:"POST", headers:this.headers()});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async completeVoiceEnrollment(principalId:string, input:{session_id:string; audio_b64:string; spoken_challenge:string; languages?:string[]}) {
    const r = await fetch(`${this.baseUrl}/v1/principals/${principalId}/voice/enrollment/complete`, {method:"POST", headers:this.headers(), body:JSON.stringify(input)});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async setDefaultEnvelope(principalId:string) {
    const r = await fetch(`${this.baseUrl}/v1/principals/${principalId}/envelope/default`, {method:"POST", headers:this.headers()});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async openLive(principalId:string, input:Record<string,unknown>={}) {
    const r = await fetch(`${this.baseUrl}/v1/principals/${principalId}/live`, {method:"POST", headers:this.headers(), body:JSON.stringify(input)});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }

  async renderDisclosure(sessionId:string, text?:string) {
    const r = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}/disclosure/render`, {method:"POST", headers:this.headers(), body:JSON.stringify(text ? {text} : {})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async confirmDisclosure(sessionId:string, deliveryEvidence:string) {
    const r = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}/disclosure/confirm`, {method:"POST", headers:this.headers(), body:JSON.stringify({delivery_evidence:deliveryEvidence})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async modelQuality() {
    const r = await fetch(`${this.baseUrl}/v1/models/quality`, {headers:this.headers()});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async transcribe(audioB64:string, language?:string) {
    const r = await fetch(`${this.baseUrl}/v1/audio/transcriptions`, {method:"POST", headers:this.headers(), body:JSON.stringify({audio_b64:audioB64, language})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async resolveAudio(audioB64:string) {
    const r = await fetch(`${this.baseUrl}/v1/audio/resolve`, {method:"POST", headers:this.headers(), body:JSON.stringify({audio_b64:audioB64})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async verificationTrustBundle(principalId:string) {
    const r = await fetch(`${this.baseUrl}/v1/principals/${principalId}/verification-trust-bundle`, {headers:this.headers()});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async createStudioCharacter(studioId:string, input:Record<string,unknown>) {
    const r = await fetch(`${this.baseUrl}/v1/studios/${studioId}/characters`, {method:"POST", headers:this.headers(), body:JSON.stringify(input)});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async issueStudioRights(studioId:string, characterId:string, input:Record<string,unknown>) {
    const r = await fetch(`${this.baseUrl}/v1/studios/${studioId}/characters/${characterId}/rights`, {method:"POST", headers:this.headers(), body:JSON.stringify(input)});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async openStudioLive(studioId:string, characterId:string, input:Record<string,unknown>) {
    const r = await fetch(`${this.baseUrl}/v1/studios/${studioId}/characters/${characterId}/live`, {method:"POST", headers:this.headers(), body:JSON.stringify(input)});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async revokeStudioRights(grantId:string, reason:string) {
    const r = await fetch(`${this.baseUrl}/v1/studio-rights/${grantId}/revoke`, {method:"POST", headers:this.headers(), body:JSON.stringify({reason})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async speak(sessionId:string, text:string, prosody?:Record<string,unknown>, agency?:Record<string,unknown>) {
    const r = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}/speak`, {method:"POST", headers:this.headers(), body:JSON.stringify({text, prosody, agency})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async attest(sessionId:string, nonce:string) {
    const r = await fetch(`${this.baseUrl}/v1/sessions/${sessionId}/attest`, {method:"POST", headers:this.headers(), body:JSON.stringify({nonce})});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  async erase(principalId:string) {
    const r = await fetch(`${this.baseUrl}/v1/principals/${principalId}/erase`, {method:"POST", headers:this.headers()});
    if (!r.ok) throw new Error(await r.text()); return r.json();
  }
  realtime(sessionId:string): WebSocket {
    const u = new URL(`${this.baseUrl.replace(/^http/,"ws")}/v1/realtime/${sessionId}`);
    u.searchParams.set("token", this.apiKey);
    return new WebSocket(u);
  }
}
