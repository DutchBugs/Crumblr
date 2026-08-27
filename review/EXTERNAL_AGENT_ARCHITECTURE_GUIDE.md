# Reviewrichting — Crumblr als instrument voor externe trading agents

**Status:** owner direction voor architectuur en review  
**Datum:** 2026-08-27  
**Scope:** richtinggevend voor ontwerp en reviews; geen gate-approval  
**Bestaande gate:** `order_send` blijft NO-GO; `feedback.2.0.md` blijft verplicht

## 1. Doel van deze handleiding

Deze handleiding legt vast hoe het reviewende en implementerende team Crumblr
verder moet ontwikkelen nu het uitgangspunt expliciet is gemaakt:

> De agents draaien buiten Crumblr. Crumblr is het betrouwbare instrument
> waarmee zij marktcontext lezen, voorstellen indienen, risico laten toetsen,
> uitvoering aanvragen en bewijs teruglezen.

Het "digitale kantoor" waarin Trading, Supervisor, Strategy, Backtest en
Training Agents leven, is dus een afzonderlijke runtime of een afzonderlijk
product. Het mag Crumblr gebruiken, maar wordt geen verborgen agent-runtime
binnen de Crumblr-codebase.

Deze richting vervangt de reeds gebouwde veiligheidsketen niet. De huidige
`DecisionCapsule`, Risk Engine, execution-time revalidation,
`ExecutionOrchestrator`, MT5-gateway, append-only historie en reconciliation
zijn juist de sterkste basis om externe agents veilig aan te sluiten.

## 2. Kernbesluiten die reviewers als norm moeten gebruiken

1. **Agents zijn externe principals.** Een agent heeft een eigen identiteit,
   versie, opdracht en runtime. Het stoppen, herstarten of vervangen van een
   agent mag Crumblr niet stoppen of herstarten.
2. **Crumblr bezit de veiligheidsautoriteit.** Risk, policy gates, sizing,
   execution, brokercredentials, idempotency, reconciliation, kill switches en
   audit blijven binnen Crumblr en zijn deterministisch waar zij financieel
   risico begrenzen.
3. **Geen agent krijgt broker- of databaseprivileges.** Agents krijgen alleen
   getypeerde, geauthenticeerde interfaces. Zij schrijven nooit rechtstreeks
   in Crumblrs database en roepen nooit rechtstreeks MT5 aan.
4. **Strategieën komen uit opdrachten en versioned artifacts.** `ict_v1` is
   geen leidende productarchitectuur. ICT en `baseline_v1` mogen blijven als
   referentie-, benchmark- en regressiefixtures, maar nieuwe strategieën worden
   niet als imports in de Crumblr-kern toegevoegd.
5. **Een directioneel voorstel bevat altijd een expliciete SL en TP.** De
   agent levert nooit lot size. Crumblr bepaalt volume uit actuele account-,
   markt-, broker- en risk-policygegevens.
6. **Een harde regel wordt nooit gereduceerd tot één score.** Risk mag
   diagnostiek en deelscores opleveren, maar financieel relevante acceptatie
   blijft `PASS`, `BLOCK` of `HALT` met machineleesbare redenen. Een hoge
   totaalscore mag een harde blokkade nooit compenseren.
7. **Een Supervisor Agent is geen veiligheidsfundament.** Semantische toetsing
   van de onderbouwing mag extern en probabilistisch zijn. Een deterministische
   Policy Gate binnen Crumblr blijft daarnaast altijd actief en kan nooit door
   de externe supervisor worden overruled.
8. **Promotie blijft menselijk.** Strategy, Backtest en Training Agents mogen
   voorstellen en bewijs produceren, maar nooit zelfstandig een strategie,
   prompt, model, risk policy of markt naar execution promoveren.

## 3. De drie verdiepingen

De verdiepingen zijn trust zones met afzonderlijke bevoegdheden. Ze hoeven
niet in één technische omgeving te draaien.

```text
┌──────────────────────────────────────────────────────────────┐
│ Verdieping 3 — Governance & Promotion                       │
│ menselijk besluit, policy, artifact registry, approvals      │
└──────────────────────────────┬───────────────────────────────┘
                               │ promote / revoke
┌──────────────────────────────▼───────────────────────────────┐
│ Verdieping 2 — Research & Learning                          │
│ Strategy Agent · Backtest Agent · Training Agent             │
│ alleen research-artifacts; geen executionrecht               │
└──────────────────────────────┬───────────────────────────────┘
                               │ approved assignment/artifact
┌──────────────────────────────▼───────────────────────────────┐
│ Verdieping 1 — Trading Operations                           │
│ Trading Agent · optionele Supervisor Agent                   │
│ voorstel en review; geen brokercredential of lot size        │
└──────────────────────────────┬───────────────────────────────┘
                               │ typed API/events
┌──────────────────────────────▼───────────────────────────────┐
│ Crumblr — betrouwbaar instrument / control plane             │
│ context · ingress · risk · policy · capsules · execution     │
│ MT5 gateway · reconciliation · journal · kill switch         │
└──────────────────────────────────────────────────────────────┘
```

### Verdieping 1 — Trading Operations

De Trading Agent ontvangt een concrete, versioned opdracht. Die opdracht
bepaalt onder meer markt, timeframe, strategy artifact, geldigheidsduur en de
bewijsvereisten. De agent leest toegestane context en dient nul of één
`TradeProposal` in voor een decision window.

De agent mag:

- markt-, portfolio- en point-in-time nieuwscontext lezen;
- `NO_TRADE` expliciet registreren;
- BUY of SELL voorstellen met entry, SL, TP, expiry, requested risk fraction
  en gestructureerde evidence;
- een eerder voorstel intrekken zolang Crumblr nog geen submission is gestart.

De agent mag niet:

- volume bepalen;
- een Risk- of Policy-uitkomst aanpassen;
- een `DecisionCapsule` zelf sealen;
- rechtstreeks een execution request, brokerorder of kill-switch-reset doen;
- vrije tekst als uitvoerbaar commando laten behandelen.

Een externe Supervisor Agent mag het voorstel en de bijbehorende evidence
beoordelen op interne tegenspraak, aansluiting op de opdracht, regime en
onvoldoende onderbouwing. Het resultaat is een getypeerde review met verdict,
reason codes, confidence/calibratie en evidence-referenties. Vraag om een
beknopte, controleerbare onderbouwing; sla geen verborgen chain-of-thought als
veiligheidsbewijs op.

### Verdieping 2 — Research & Learning

De Strategy Agent produceert geen actieve Python-import voor Crumblr, maar een
immutable `StrategyArtifact` en een `StrategySpecification`. Daarin staan
ten minste:

- strategy id en semantische versie;
- artifact hash en producer identity;
- hypothese, markt, timeframe en verwachte holding period;
- benodigde features en databronnen;
- entry-, exit-, SL- en TP-regels;
- ondersteunde market capabilities;
- kosten-, latency- en fill-aannames;
- bekende failure modes en verboden regimes;
- evaluatie- en promotiecriteria.

De Backtest Agent voert een reproduceerbare testopdracht uit tegen een
vastgezette dataset, codeversie, cost model en random seed. De output is een
immutable `BacktestReport`, inclusief alle beslissingen, vetoes, fills,
rejections, regime-uitsplitsingen en out-of-sample resultaten. Een winstcijfer
zonder de volledige provenance is geen geldig artifact.

De Training Agent analyseert niet alleen uitgevoerde trades, maar alle relevante
decision windows:

- `NO_TRADE`;
- door Risk geblokkeerde voorstellen;
- door Policy of Supervisor geweigerde voorstellen;
- verlopen of technisch ongeldige voorstellen;
- uitgevoerde trades en hun brokerresultaat;
- counterfactual uitkomsten van afgewezen voorstellen.

Daarbij moeten vier oorzaken gescheiden blijven: kwaliteit van de strategie,
kwaliteit van de uitvoering, juistheid van de policy en kwaliteit van de data.
Een afgewezen trade die achteraf winstgevend lijkt, bewijst niet dat de afwijzing
fout was. Counterfactuals moeten met hetzelfde causale fill- en kostenmodel
worden berekend en expliciet als hypothetisch blijven gemarkeerd.

De output van Training is een `TrainingFinding` of
`StrategyChangeProposal`. Die kan nooit automatisch productiecode, prompts,
modellen, risk limits of assignments wijzigen.

### Verdieping 3 — Governance & Promotion

Hier worden artifacts geregistreerd, ondertekend, gereviewd, gepromoveerd,
gepauzeerd en ingetrokken. Minimaal vereist:

- scheiding tussen auteur, evaluator en promotor;
- expliciete environment- en market-scope;
- één actieve champion per scope en onbeperkte shadow challengers;
- reviewbare policy- en assignmentversies;
- rollback naar een eerder artifact;
- menselijke goedkeuring voor iedere stap richting broker submission;
- geen self-promotion door een agent die het artifact maakte of testte.

## 4. De doelketen

De externe agentgrens komt vóór de huidige intent-time Risk Engine. De
downstream Phase-4-keten blijft zoveel mogelijk ongewijzigd.

```text
ContextBundle door Crumblr
        ↓
externe Trading Agent
        ↓
TradeProposal / NO_TRADE
        ↓
Agent Gateway
  auth · assignment · schema · expiry · idempotency · evidence
        ↓
platform-owned TradeIntent
        ↓
intent-time Risk Engine
        ↓
deterministische Policy Gate
        ↓
optionele externe Supervisor review
        ↓
platform seal van DecisionCapsule
        ↓
bestaande ExecutionOrchestrator
        ↓
fresh broker observation · reconciliation · FINAL Risk
        ↓
order_check · later gated order_send · post-fill reconciliation
```

Een externe supervisor timeout, ongeldige response of ontbrekende review is
`UNKNOWN` en dus geen approval. Een externe approval kan een Risk `BLOCK` of
`HALT` nooit herstellen. Crumblr sealt uitsluitend een executeerbare capsule
wanneer alle verplichte authorities aantoonbaar hebben ingestemd.

## 5. Minimale nieuwe contracten

De precieze naam mag wijzigen, maar de semantiek niet.

### `AgentIdentity`

Bevat `agent_id`, type/role, runtime version, model provider/version indien van
toepassing, public key/service identity, status en capability claims. Een
weergavenaam is geen identiteit.

### `TradingAssignment`

Bevat minimaal:

- `assignment_id` en version/hash;
- toegestane `agent_id`;
- market, instrument en timeframe;
- strategy artifact id/hash;
- geldigheidsinterval;
- maximaal voorsteltempo;
- toegestane requested-risk band;
- vereiste evidence en supervisor policy;
- environment en champion/shadow status.

### `DecisionContextBundle`

Bevat immutable referenties naar wat de agent mocht zien: market snapshot,
bar/tick window, instrument spec, portfolio summary, session, data-quality,
policy hints en point-in-time nieuwsdata. Het bundle heeft een expiry en
content hash. Het mag geen brokercredentials of mutatierechten bevatten.

### `TradeProposal`

Bevat minimaal `proposal_id`, `agent_id`, `assignment_id`, context hash,
strategy artifact hash, side, entry type/reference, SL, TP, expiry, confidence,
requested risk fraction, reason codes en gestructureerde evidence-referenties.
Een directioneel voorstel zonder SL of TP wordt bij ingress geweigerd en bereikt
de Risk Engine niet.

De Agent Gateway maakt na succesvolle boundary-validatie de platform-owned
`TradeIntent`. Daardoor blijven de huidige Risk- en executioncontracten het
gezaghebbende interne formaat en hoeft externe input niet rechtstreeks als
vertrouwd domeinobject te worden behandeld.

### `SupervisorReview`

Bevat de exacte proposal/intent fingerprint, supervisor identity en versie,
policy/task versie, verdict, reason codes, evidence claims, calibratie en
expiry. De review kan niets aan side, prijs, SL, TP of risk veranderen.

### Researchcontracten

Voeg minimaal `StrategyArtifact`, `BacktestRequest`, `BacktestReport`,
`EvaluationRecord`, `TrainingFinding` en `StrategyChangeProposal` toe. Alle
artifacts zijn immutable, content-addressed en verwijzen naar hun inputs.

## 6. Wat de huidige code al goed doet

Reviewers moeten de volgende delen beschermen tegen een grote herbouw:

- `TradeIntent` laat de agent geen volume bepalen;
- Risk gebruikt harde `PASS/BLOCK/HALT`-besluiten en machineleesbare redenen;
- de huidige supervisor kan vetoën maar een trade niet wijzigen;
- featurewaarden, market data, broker snapshots, decisions en capsules worden
  duurzaam vastgelegd;
- execution requests worden vóór brokerinteractie immutable geclaimd;
- de approval chain is inhoudelijk gefingerprint;
- execution gebruikt een verse, coherente brokerobservatie, reconciliation en
  FINAL Risk;
- de echte MT5-adapter kan nu alleen `order_check`; `order_send` is structureel
  onmogelijk;
- een onbekende of onveilige toestand faalt gesloten.

Review 1.24 heeft deze Phase-4 execution-keten formeel goedgekeurd. Nieuwe
agentarchitectuur mag die status niet impliciet terugdraaien of omzeilen.

## 7. Huidige verschillen met de doelarchitectuur

Het reviewteam moet de volgende punten als expliciete migratiegaten behandelen:

1. `trading_agent/registry.py` laadt `baseline_v1` en `ict_v1` als lokale
   Python-callables. Dit is een in-process strategy plugin, geen externe agent.
2. `LiveDecisionOrchestrator` selecteert en draait die strategie zelf. De
   observatie/decision/execution-scheiding is goed, maar de agentgrens ontbreekt.
3. `PlatformConfig` kent één globale `trading_agent.strategy_id`; er is nog geen
   agent identity, assignment, champion/shadow scope of artifact registry.
4. `TradeIntent` en `DecisionCapsule` dragen strategy/modelversies, maar nog
   geen betrouwbare `agent_id`, `assignment_id`, runtime/prompt versie of
   strategy artifact hash.
5. `api/` en `backtest/` bevatten nog geen werkende agent- of researchservice.
6. `EvaluationCompleted` bestaat als eventcontract, maar een volledige
   post-trade evaluator en Training Agent-loop ontbreken.
7. Er is nog geen point-in-time nieuwscontract of nieuwsarchief. Nieuws dat een
   agent buiten Crumblr leest is zonder evidence snapshot niet reproduceerbaar.
8. Nieuwe paden bevatten nog EUR/USD- en M5-defaults; echte multi-market
   ondersteuning vereist expliciete capability profiles, calendars en cost
   models per markt.
9. `CODE_COMMIT = "uncommitted-prototype"` is nog hard-coded in de replay- en
   live-decisionpaden en is onvoldoende als productieprovenance.
10. De huidige directionele `TradeIntent` vereist een SL maar laat TP nog
    optioneel. Dat wijkt af van de owner direction voor agentvoorstellen en
    moet vóór de externe agent-canary contractueel worden opgelost.

## 8. Migratie zonder de veiligheidsketen te destabiliseren

### Stap A — architectuurcontracten, nog zonder execution

- Leg een ADR vast voor de externe agentgrens en trust zones.
- Ontwerp de contracten uit §5 en threat-model de ingress.
- Voeg agent/assignment/artifact provenance append-only toe.
- Laat alle nieuwe endpoints standaard read-only of shadow-only zijn.
- Verander niets aan de bestaande `order_check`-autorisatie of `order_send`
  NO-GO.

### Stap B — externe Trading Agent in shadow

- Bouw een Agent Gateway die een `TradeProposal` valideert en duurzaam
  registreert.
- Laat één externe Trading Agent tegen historische replay en live shadow data
  draaien.
- Draai de huidige in-process strategy uitsluitend als vergelijking/twin.
- Bewijs idempotency, timeouts, agentuitval, conflicterende retries, ongeldige
  assignments en restart recovery.

### Stap C — supervisorgrens

- Benoem de huidige `evaluator.pretrade` conceptueel als deterministische
  Policy Gate.
- Voeg de externe Supervisor Agent als aparte authority toe.
- Bewijs dat supervisoruitval fail-closed is en dat hij geen Risk-blokkade kan
  overstemmen of een intent kan muteren.

### Stap D — research- en trainingvlak

- Bouw eerst de artifact registry en reproduceerbare Backtest Requests.
- Hergebruik dezelfde strategy-, risk-, policy- en fillsemantiek als live waar
  mogelijk; documenteer ieder verschil.
- Bouw daarna de evaluator die ook afwijzingen en `NO_TRADE` labelt.
- Laat Training alleen change proposals produceren.

### Stap E — eerste agentgedreven DEMO-canary

De geautoriseerde non-sending real-terminal `order_check` mag volgens review
1.24 doorgaan zonder op deze migratie te wachten: dat bewijst de brokergrens,
niet de agentarchitectuur.

Een canary op basis van de huidige in-process strategy mag eveneens uitsluitend
als execution-platformbewijs worden beschreven. De eerste canary die als
**autonome agent trading** wordt gepresenteerd, moet door de externe agentgrens
lopen en daarnaast alle bestaande voorwaarden voor `feedback.2.0`, Submission
Gate, ambiguous-outcome recovery, flatten en post-fill reconciliation halen.

## 9. Reviewacceptatiecriteria

Een external-agent wijziging is pas reviewbaar wanneer bewijs bestaat voor:

- een agentproces kan verdwijnen zonder dat Crumblr onveilig wordt;
- service identity, authorization en assignment scope worden afgedwongen;
- hetzelfde proposal id plus dezelfde inhoud is idempotent;
- hetzelfde proposal id plus andere inhoud faalt gesloten als conflict;
- een te late, dubbele, ongeldige of onbekende agentresponse wordt geweigerd;
- geen enkele agent kan lot size, broker symbol, risk verdict of executionstate
  rechtstreeks schrijven;
- iedere proposal, `NO_TRADE`, rejection en timeout is auditbaar;
- evidence verwijst naar immutable point-in-time input;
- externe vrije tekst wordt nooit als commando of policy behandeld;
- Risk en Policy zijn deterministisch te replayen;
- een externe Supervisor is veto-only en faalt gesloten;
- research artifacts kunnen niet zonder menselijke promotie actief worden;
- backtestdata en labels zijn causaal en vrij van look-ahead;
- strategie-, agent-, opdracht-, model/prompt-, config-, data- en codeversies in
  de provenance zijn terug te vinden;
- markt en timeframe komen uit assignment/capability, niet uit verborgen
  EUR/USD/M5-defaults;
- de bestaande Phase-4 execution invariants ongewijzigd blijven slagen.

## 10. Red flags die reviewers direct moeten terugsturen

- nieuwe strategiecode importeren in Crumblrs runtime als primaire
  uitbreidingsmethode;
- agents rechtstreeks tabellen laten schrijven of MT5 laten benaderen;
- vrije agent-chat als integratieprotocol;
- alleen goedgekeurde of uitgevoerde trades bewaren;
- een samengestelde score gebruiken om een harde Risk-fout te compenseren;
- het advies van een LLM als bewijs zien zonder onderliggende datarefs;
- een strategy id vertrouwen zonder immutable artifact hash;
- nieuws zonder publicatietijd, revisiestatus en snapshotreferentie gebruiken;
- afgewezen trades achteraf met gunstigere fill-aannames beoordelen;
- backtest, live en training elk een eigen feature-implementatie geven;
- een Training of Strategy Agent zichzelf laten promoveren;
- ICT-aannames in generieke core-contracten of market adapters vastleggen;
- een agenttimeout of onbekende state als approval behandelen;
- de externe agentmigratie gebruiken als aanleiding om de huidige execution-
  en reconciliationketen te herschrijven.

## 11. Opdracht aan het reviewende team

Behandel deze handleiding vanaf de volgende betekenisvolle review als owner
direction naast `build.md`, `status.md`, `review/domain_contracts.md` en de
nieuwste `feedback.*.md`.

Vraag het implementerende team eerst om een klein, reviewbaar ontwerp-pakket:

1. ADR voor externe agents en trust boundaries;
2. threat model voor Agent Gateway en news/evidence input;
3. eerste versies van `AgentIdentity`, `TradingAssignment`,
   `DecisionContextBundle`, `TradeProposal` en `SupervisorReview`;
4. migratieplan dat de bestaande `DecisionCapsule` → executionketen behoudt;
5. testmatrix voor identity, authorization, idempotency, expiry, agentuitval,
   replay en fail-closed gedrag;
6. expliciete afbakening van wat vóór de eerste agentgedreven DEMO-canary moet
   zijn gebouwd en wat pas daarna nodig is.

Open bevindingen afzonderlijk voor:

- **financial-safety blockers** — blokkeren iedere broker submission;
- **agent-boundary blockers** — blokkeren het label "agentgedreven";
- **research-integrity blockers** — blokkeren strategiepromotie;
- **scaling gaps** — blokkeren een nieuwe market capability, maar niet per se
  de huidige EUR/USD DEMO-evidence.

Deze handleiding sluit geen bestaand finding, passeert geen milestone en geeft
geen toestemming voor `order_send`. Zij bepaalt wel de architectuur waarlangs
nieuwe agent-, strategie-, backtest- en trainingfunctionaliteit moet landen.
