# イベントカード仕様 — TERRA ASCENDANT

> [scenario.md](scenario.md) の各 Act を、**実装可能な粒度のイベントカード**に落とし込んだもの。
> 確定した役割モデル（神＝**能動型 director**：能動的に起こし＋決着も裁定／コストは **Mandate**）に基づく。
> 数値は **基準値（実装で調整）**。M0以降は本仕様をそのまま `data/events/*.json` 化してデータ駆動する（§末にサンプル）。

---

## 1. 共通スキーマ

各イベントカードは以下の構造を持つ。

| フィールド | 意味 |
|---|---|
| `id` | 一意ID（例 `clim_megadrought`） |
| `era` | 発生しうる時代／Act（`act1`〜`act5`、または `any`） |
| `category` | `climate` / `economy` / `geopolitics` / `pandemic` / `science` / `space_env` / `public_will` / `operational` |
| `layer` | `god`（神が能動起動）/ `autonomous`（システム自然発生→神は裁定のみ）/ `national`（自国内アクター） |
| `scope` | `global`（全勢力に作用）/ `faction`（特定勢力）/ `national`（自国のみ） |
| `trigger` | 発生条件（KPI閾値・確率・前提イベント）。`god` 層は「起動コスト= `mandate_cost`」を支払えば任意起動可 |
| `mandate_cost` | 神が能動起動する際に消費する Mandate（`autonomous`/裁定のみは 0） |
| `narrative` | 提示文（淡々としたドキュメンタリ調） |
| `choices[]` | プレイヤーの選択肢。各 choice は `cost` / `effects` / `uncertainty` を持つ |

**choice の構造**
- `cost`：起動・選択に要する資源（`influence` / `funding` / `mandate`）。
- `effects`：確定効果。KPIデルタ（§game_design 6.2）＋勢力別効果。
- `uncertainty`：確率分岐。`{p, label, effects}` の配列で、宇宙の“出たとこ勝負”を表現。

**効果が触れるKPI（基準スケール 0–100、人口/$/kg は絶対値）**
`spacefaring` / `offworld_pop` / `self_sufficiency` / `cohesion` / `public_will` / `knowledge` / `risk_debt` / `mandate`
＋ 勢力別：`faction.<id>.{budget, trust_to_player, capability}`

---

## 2. Act I — 軌道の橋（2030s）

### `clim_megadrought` — 大規模気候災害（神・能動）
- **layer/scope/category**：`god` / `global` / `climate` ／ **mandate_cost: 12**
- **narrative**：連動する熱波と干ばつが複数大陸を襲う。各国の予算に「足元優先」の圧力がかかる。だが報道は静かに問いかける——「我々の揺りかごは、いつまで安全なのか」。
- **choices**
  1. **そのまま世界を試す（裁定：放置）**
     - effects: 全勢力 `budget −15%`、`public_will −8`、長期フラグ `backup_planet_sentiment +1`（火星世論の伏線）
  2. **自国を災害対応に全振り（national 対応）**
     - cost: `funding`（自国宇宙予算から転用）
     - effects: 自国 `public_will +10`、自国 `spacefaring −10`（その窓は出遅れる）、`cohesion +5`
  3. **国際救援を主導し外交点に変える**
     - cost: `influence 20`
     - effects: `cohesion +12`、自国 `trust_to_player(全勢力) +8`
     - uncertainty: `{p:0.3, "救援失敗で批判", {public_will −10, cohesion −5}}`

### `econ_boom` / `econ_bust` — 世界経済サイクル（神・能動）
- **layer/scope/category**：`god` / `global` / `economy` ／ **mandate_cost: 8**
- **narrative**：資本が宇宙に流れ込む春か、すべてが細る冬か。神はその位相を選べる。
- **choices**
  - **好況を呼ぶ**：全勢力 `budget +20%`、民間 `capability +10`、ただし `risk_debt +5`（過熱）
  - **不況を呼ぶ**：全勢力 `budget −20%`、商業共生国家は追加 `−15%`（市場依存の脆さ）、`public_will −5`

### `oper_first_fatal_launch` — 最初の死者を出す打ち上げ事故（自律→神が裁定）
- **layer/scope/category**：`autonomous` / `faction` / `operational` ／ **mandate_cost: 0**
- **trigger**：いずれかの勢力の `risk_debt > 60` で確率発生（自国にも起こりうる）
- **narrative**：上昇中の機体が爆発四散。乗員は還らない。世界が安全と速度の天秤を見つめる。
- **choices（神の裁定＝世界世論をどちらへ傾けるか）**
  1. **規制強化へ**：全勢力 `risk_debt −15`・`spacefaring −8`（安全だが減速）、`cohesion +5`
  2. **自己責任論へ**：全勢力 `spacefaring +6`・`risk_debt +10`（速いが脆くなる）、`public_will −5`
  - ＋自国が当事者なら：自国 `public_will −18`・`trust −10`

### `debris_treaty` — デブリ規制条約（自律→裁定／national 参加判断）
- **layer/category**：`autonomous` / `geopolitics`
- **trigger**：軌道混雑度が閾値超で議題化
- **choices**：自国が**批准**（`cohesion +8`, 自国 `spacefaring −4` 短期コスト）／**拒否**（自国 `spacefaring +0`, `trust −6`, ケスラー連鎖確率↑）

---

## 3. Act II — 足場としての月（2040s）

### `geo_resource_nationalism` — 月資源ナショナリズム（神・能動）
- **layer/scope/category**：`god` / `global` / `geopolitics` ／ **mandate_cost: 14**
- **narrative**：水氷と希少金属の鉱区を巡り、旗が立ち始める。共有のレジームか、囲い込みか。
- **choices**
  1. **囲い込みを煽る**：`cohesion −15`、各ブロック内 `capability +8`（局所最適）、共同事業 `−1`
  2. **共有レジームを後押し**：cost `influence 25`、`cohesion +15`、全勢力 `knowledge +6`（薄く広く底上げ）
     - uncertainty: `{p:0.25, "大国が離脱", {cohesion −8}}`

### `oper_first_lunar_death` — 月面での最初の死（自律→裁定）
- **trigger**：月拠点の生命維持/着陸技術が未成熟（`knowledge` 閾値未満）で運用時に確率発生
- **narrative**：地球から3日の距離での死。近いがゆえに、世界はそれを直視する。
- **choices（裁定）**：**追悼と前進**（`public_will −6`, `cohesion +6`, `mars_momentum 維持`）／**計画見直し**（`risk_debt −12`, 全勢力 `spacefaring −6`, 火星機運フラグ `−1`）

### `tech_isru_proven` — 月ISRU実証（national 偉業／成功で Mandate 回復）
- **layer/category**：`national`（自国が達成しうる）→ 成功は **global な意味**を持つ
- **trigger**：自国が ISRU 技術ライン（水氷採掘→電解→推進剤合成）を完了
- **effects**：自国 `capability +15`・`trust +10`、**`mandate +15`（人類の偉業による天命回復）**、Act III の火星帰還燃料リスク `−大`
- **note**：これを月で枯らせたかが Act III の生死を分ける“予行演習”。

---

## 4. Act III — 赤い窓（2050s／初の有人火星）

### `space_solar_storm` — 太陽嵐 SPE（神・能動／タイミング兵器）
- **layer/scope/category**：`god` / `global` / `space_env` ／ **mandate_cost: 10**
- **narrative**：太陽が荒れる。いま遷移軌道にある船は、磁気圏の外にいる。
- **trigger**：神は太陽極大期の任意タイミングで起動可。遷移中の有人ミッションがあるほど重い。
- **choices（神の起動）＋ 各船の生死は設計依存**
  - 起動すると、遷移中の全有人船に判定：
    - 遮蔽/避難シェルター設計済み → `{p:0.85, 乗員生存・被曝 +小}`
    - 未設計 → `{p:0.55, 乗員死亡}`、生存しても `health −大`
  - 全滅が出れば → `event: pub_space_winter` を誘発
- **設計意図**：神がライバルの船団を狙って嵐を起こせるが、**自国の船も遷移中なら巻き添え**（無差別性の体現）。

### `oper_mars_first_footprint` — 人類初の火星着陸（自律/national の競争 → 裁定）
- **trigger**：いずれかの勢力が有人火星 EDL に成功
- **narrative**：薄い大気を裂いて、最初の脚が赤い土を捉える。誰の足であれ、それは人類の足だ。
- **effects（成功）**：**全世界 `public_will +25`・`mandate +20`**、Act IV 資金解禁
  - 自国が一番乗り → 自国 `trust +20`・影響力で他を圧倒
  - ライバルが先 → 自国 `public_will +10`（人類全体は前進）。“勝利は人類のもの”の最初の実感
- **失敗（全乗員喪失）** → `pub_space_winter`

### `pub_space_winter` — 宇宙の冬（最悪分岐／自律→裁定）
- **trigger**：有人火星ミッションの全滅、または `public_will < 15`
- **narrative**：世界が宇宙から目を背ける。予算は引き上げられ、計画は凍る。
- **effects**：全勢力 `budget −40%`・`spacefaring −20`、火星計画フラグ凍結（数十年）
- **choices（どう冬を越すか）**：
  1. **細々と灯を守る**：cost `influence 大`、自国 `knowledge` 維持、回復は遅いが基盤を残す
  2. **撤退して国内に資源を戻す**：自国 `public_will +10` 短期回復、宇宙基盤 `−大`（次代に重い負債）

---

## 5. Act IV — 種をまく（2060s–2080s）

### `geo_earth_crisis` — 地球規模危機による補給途絶（神・能動／最大のドラマ源）
- **layer/scope/category**：`god` / `global` / `geopolitics|pandemic|economy` ／ **mandate_cost: 16**
- **narrative**：地球が燃える（戦争／恐慌／疫病）。火星行きの補給船は、今期、来ない。
- **effects**：火星定住地への補給を1〜2窓ぶん停止。
  - 定住地 `self_sufficiency ≥ 70` → 耐え抜く：**`self_sufficiency +10`（真の定住の証明）・`public_will +15`**
  - `self_sufficiency < 70` → 危機：`offworld_pop −大`、撤退判断 or 犠牲
- **設計意図**：神が起こす危機が、プレイヤー自身の「自立度をどこまで上げたか」を否応なく採点する。

### `space_dust_storm` — 火星規模の大塵嵐（神・能動）
- **category/scope**：`space_env` / `faction`(火星拠点群) ／ **mandate_cost: 9**
- **effects**：太陽光発電 `−大` → エネルギー収支危機。原子力/蓄電に投資済みなら緩和、未投資なら `offworld_pop` リスク。

### `nat_mars_identity` — 火星アイデンティティ（national／統治の岐路）
- **layer/category**：`national` / `public_will`
- **trigger**：火星生まれ世代が一定人口に到達
- **narrative**：「我々は地球人ではない」。火星生まれの世代が自治を求め始める。
- **choices**：**自治を認める**（`self_sufficiency +8`・火星 `public_will +12`、ただし自国の統制 `−`）／**統制を維持**（自国統制 `+`、火星 `unrest +大`・離反リスク）

---

## 6. Act V — 揺りかごの外（2090s–2130s）

### `sci_terraform_question` — テラフォーミングの是非（神・能動／超長期の賭け）
- **layer/scope/category**：`god` / `global` / `science` ／ **mandate_cost: 25（最大級）**
- **narrative**：数世紀計画。最初の不可逆な一歩を、いま人類が刻むべきか。
- **choices**：**起動する**（超長期テラフォーミング進行フラグ、`knowledge +大`、エンディング分岐「夜明け」）／**与圧都市での共生に留める**（堅実、別エンディング）
- **uncertainty**：起動は世代をまたぐ賭け。途中の地球危機・資源で頓挫しうる。

### `geo_two_world_tension` — 二元文明の力学（神・能動）
- **category/scope**：`geopolitics` / `global` ／ **mandate_cost: 14**
- **effects**：地球—火星関係を緊張/独立/協調へ傾ける。`self_sufficiency` が高いほど火星は独立に振れやすい。

### `sci_paradigm_shift` — 科学パラダイム転換（神・能動／any era）
- **category**：`science` ／ **mandate_cost: 18**
- **effects**：技術ツリーに新分岐を解禁（例：核熱推進の成熟、準閉鎖生態系）。全勢力 `knowledge +大`・`spacefaring +`。

---

## 7. データ駆動サンプル（実装ブリッジ）

M0以降、本仕様は下記のような JSON としてエンジンが読む（`data/events/act1_climate.json` 等）。
ルールは `core/`、数値は `data/` という原則（game_design §10）に従う。

```json
{
  "id": "clim_megadrought",
  "era": "act1",
  "category": "climate",
  "layer": "god",
  "scope": "global",
  "mandate_cost": 12,
  "trigger": { "type": "god_activated", "min_mandate": 12 },
  "narrative": "連動する熱波と干ばつが複数大陸を襲う。各国の予算に『足元優先』の圧力がかかる……",
  "choices": [
    {
      "id": "let_it_test",
      "label": "そのまま世界を試す",
      "cost": {},
      "effects": {
        "factions_all": { "budget_pct": -15 },
        "kpi": { "public_will": -8 },
        "flags": { "backup_planet_sentiment": 1 }
      },
      "uncertainty": []
    },
    {
      "id": "lead_relief",
      "label": "国際救援を主導する",
      "cost": { "influence": 20 },
      "effects": { "kpi": { "cohesion": 12 }, "trust_to_player_all": 8 },
      "uncertainty": [
        { "p": 0.3, "label": "救援失敗で批判", "effects": { "kpi": { "public_will": -10, "cohesion": -5 } } }
      ]
    }
  ]
}
```

> このスキーマは M0 のシミュレーションコアがそのまま消費できるよう、KPI 名・勢力効果の語彙を game_design §6.2 と一致させてある。
> 新イベントの追加＝JSON 追加だけで済む（コード改変不要）= モッダブルでバランス調整しやすい。
