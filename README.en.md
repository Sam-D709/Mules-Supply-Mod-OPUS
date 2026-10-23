# Mules-Supply-Mod-OPUS

**Public pre-release: [v0.1.0-beta.3](https://github.com/Sam-D709/Mules-Supply-Mod-OPUS/releases/tag/v0.1.0-beta.3), internal version 665.** [Release notes](docs/releases/v0.1.0-beta.3.md). Ship logs now contain compact dispatch results and throttled idle summaries. Detailed disk logging is optional and off by default; procurement rules remain those of beta.1.

[简体中文](README.md) | **English**

A supply-focused mod for **X4: Foundations**, based on **Mules, Supply and Warehouses Extended 6.60**. It mainly rewrites **M4 — Supply Mule**: allocate ships by actual cargo capacity, combine procurement lots, and complete small remaining demands. M1, M2 and M3 remain available.

Existing gameplay feedback is from **X4 8.00 Hotfix 4 (legacy) with Star Wars Interworlds**. Unmodded 8.00 and 9.00 have not been gameplay-tested. The latest player-supplier preference and localization changes still need native-game verification.

## Download and install

Download **`Mules-Supply-Mod-OPUS-v0.1.0-beta.3.zip`** from [Releases](https://github.com/Sam-D709/Mules-Supply-Mod-OPUS/releases/tag/v0.1.0-beta.3). Choose the named mod ZIP under **Assets**, rather than GitHub's automatically generated **Source code** archives. GitHub Packages is not used for this mod.

This is a public beta, with X4 internal version **665**. The public version is stored in `VERSION`; maintainers can follow the [versioning and publishing guide (Chinese)](docs/VERSIONING.zh-CN.md).

1. Exit X4 and back up your saves. When upgrading, move the old `mules_and_warehouses_extended` folder outside `extensions` before installing the replacement.
2. Extract the downloaded ZIP. It contains one folder: **`mules_and_warehouses_extended`**.
3. Drag that entire folder into your X4 **`extensions`** directory. No build tools, source compilation or language configuration are required.
4. Launch X4 and confirm **Mules-Supply-Mod-OPUS** is enabled in Extensions. Configure M4 on your ships.

The result must look like this:

```text
X4 Foundations/
└── extensions/
    └── mules_and_warehouses_extended/
        ├── content.xml
        ├── ext_01.cat
        ├── ext_01.dat
        ├── README.md
        └── README.en.md
```

For example: `E:\SteamLibrary\steamapps\common\X4 Foundations\extensions\mules_and_warehouses_extended\content.xml`.

This replaces the original Mules mod and retains extension ID `ws_2013772865` for saved-order compatibility. **Do not enable both versions.** Do not nest the extracted folder inside another mod folder. Steam Workshop updates may overwrite the original mod directory. The ZIP includes Chinese and English resources; X4 selects them using its game language. The explicit English `l044` resource is generated from the default English text during packaging.

Existing settings may be preserved: change old ships from one procurement lot to three if desired. The retired Player Buy Mod percentage is not converted into the new stock threshold, which defaults to 50%.

## Dependencies and optional mods

Core procurement and compact ship logs have **no mandatory third-party mod dependency**. No additional DLC requirement is declared.

| Mod | Requirement | Purpose |
|---|---|---|
| [SirNukes Mod Support APIs](https://steamcommunity.com/sharedfiles/filedetails/?id=2042901274) | **Optional; required for the Debug menu** | Supplies Extension Options. Without it, the menu is unavailable and detailed Debug defaults to off; core procurement is independent. |
| kuertee UI Extensions and HUD | Not required | The menu calls SirNukes APIs directly, with no direct UIX API dependency. |
| Civilian Fleets | Optional compatibility | Detected to avoid duplicate subordinate behavior synchronization. |
| Subordinate Order Access | Optional convenience | Helps manage subordinate orders; not a runtime dependency. |

SWI is the current test environment, not a dependency. ship_sorter, TacticalResponsePolice and Options Helper are not required. The original Mules mod is included/replaced, not a separate prerequisite. The `-scriptlogfiles` startup flag is only needed for detailed disk logging.

## M4 settings compared with the original

Defaults below apply to newly configured behaviors.

| Original 6.60 setting | This mod | Reason |
|---|---|---|
| Max Trades: fixed at 1 | **Maximum Procurement Lots Per Trip**: 1–10, default 3 | Combine pickups without letting one ship monopolize every tiny demand. One supplier, one ware and one receiver count as a lot; delivery is not counted again. |
| Fill Cargo Hold %: 25–95%, default 75% | **Minimum Total Trip Load %**: 0–95%, default 75% | Apply the threshold to the combined trip. A secondary lot may be small; remaining demand can use relaxed tail allocation. |
| Collect from Player | **Prefer Player-Owned Suppliers**: enabled by default | Prefer eligible owned supply when on. When off, owned supply remains eligible and competes with NPC supply normally. |
| Player Buy Mod: 0–100% price multiplier | **Player Supplier Stock Threshold %**: 0–100%, default 50% | Keep low-stock sales factories out of internal supply while letting stocked factories support logistics. This is an eligibility trigger, not a retained reserve. |
| % of trades to eval | Removed from the visible menu | Shared discovery and batched processing handle search work; players do not need to tune a candidate percentage. |
| Allow Build Storage | **Supply Construction Storage**: enabled by default | Deliver construction materials to the receiver's build storage; this does not collect materials from build storage. |
| Allow Resources | **Supply Externally Produced Consumables**: enabled by default | Supply wares the receiver consumes but does not produce, such as microchips for an advanced electronics factory. Not limited to ore or gas. |
| Allow Intermediates | **Supply Self-Produced Consumables**: enabled by default | Supply wares the receiver both produces and consumes when internal production cannot keep up. |
| Allow Trade Wares | Removed from the visible menu | Supply trade inventory from valid buy offers. The inherited special filter for final products still applies. |
| Serve Source Only | **Supply Home Station/Sector Only**: disabled by default | Restrict recipients to the home station and its build storage, or player stations in the home sector. Suppliers may still be elsewhere within range. |
| No equivalent | **Secondary corridor radius (jumps)**: 0–10, default 2 | Limit secondary suppliers to sectors near the fixed home-to-primary-supplier route, also respecting the home range. |
| No equivalent | **Price markup per off-route jump %**: 0–50%, default 5% | Represent detour cost when comparing suppliers of the same secondary ware, without changing actual payment. |
| Home Station/Sector; Assign Ship To Station; Max Jumps From Home | Retained | Set the service anchor and range. Station assignment requires a specific eligible player station. |
| Collect from AI Factions; Lock Ware List; Wares | Retained | Independently control NPC sources and which wares may be procured. |

With a player-sector home and home-only service enabled, M4 supplies player stations in that sector, not NPC recipients. NPC stations can still be suppliers. An explicitly selected NPC station home follows the inherited NPC service rules.

## Allocation and examples

Ships sharing a home share demand and supplier information. Capacity groups consider ship model, actual capacity and cargo transport capabilities; XL/L/M/S labels do not determine allocation order.

**Normal allocation runs from largest capacity to smallest.** Each ship combines up to its lot limit and checks the total trip load. **Tail allocation then runs from smallest to largest and waives only the minimum load.** A busy small ship does not prevent an idle larger ship from taking remaining demand. Other trade requirements and the lot limit remain in force.

Assume two 16,000 m³ A ships, five 5,000 m³ B ships, a 90% load threshold, a three-lot limit, and 1 m³ per unit. All examples require valid sources, prices, routes, docking, funds and drones.

| Demand | Allocation example |
|---|---|
| 9,000 energy cells | A cannot meet its 14,400 m³ threshold. One B takes 5,000; another takes the remaining 4,000 during relaxed tail allocation. |
| 9,000 energy cells and 6,000 BoFu | A may combine both eligible pickups: 15,000 m³, or 93.75%. It buys both before returning to deliver. Secondary sources must fit the configured corridor. |
| 9,000 energy cells and 5,000 BoFu | The two lots total 87.5%, below 90%. A needs a third suitable lot or allocation proceeds to other ships/tail handling. |
| One unit each of four different wares | During tail allocation, a B can take up to three lots; another takes the fourth. If all B ships are busy, an idle A can handle the remainder. |
| 9,000 energy cells split across two factories, plus 5,500 BoFu | A may combine 6,000 cells, 5,500 BoFu and 3,000 cells as three lots: 14,500 m³, or 90.625%. |
| 2,000 cells remain and all B ships are busy | An idle A can take the tail lot without waiting for B to return. If every ship is busy, demand waits for capacity. |

One trip serves a single receiver and uses one compatible cargo transport type. Demand volume is compared within service-scope and ware-use priority buckets; this is not a global optimization across every receiver and ware.

## Owned supply, distance and secondary pickups

**Owned stock eligibility applies with preference both on and off:**

```text
current stock × 100 > allocated storage for this ware × threshold percentage
```

Equality is not sufficient; zero allocation is skipped. With 10,000 units allocated, 8,000 stocked and a 60% threshold, a valid shipment may take 7,000 units and leave 1,000. The threshold does not require keeping 60% after shipment.

- **Preference on:** for each ware, choose an executable eligible owned supplier before NPC suppliers, even if the owned supplier is farther away within range. If owned supply is unavailable or insufficient, NPC lots may cover the remainder within the lot limit.
- **Preference off:** eligible owned and NPC suppliers use the same ranking rules. Owned supply is not disabled. Owned stock below the threshold is still excluded.
- The NPC switch controls actual NPC sources only. Turning preference off does not change ownership, free internal transfers, or the owned-transfer profit-spread exception.

Within one priority tier, the **primary supplier** is chosen by legal jumps from home, then actual purchase price including available discounts. For example, an owned supplier two jumps away beats an NPC one jump away with preference on; preference off favors the nearer NPC.

After choosing the primary supplier, the home-to-primary route stays fixed. **Secondary suppliers** must remain within both the home range and the corridor radius. Within the same priority tier, suppliers of the same ware compete using:

```text
comparison price = actual unit price × (1 + off-route jumps × markup / 100)
```

At 5% per jump, an on-route price of 103 beats a one-jump-off-route price of 100, whose comparison price is 105. Actual payment remains 100 if that off-route source is selected. A sole valid source is not rejected simply because the comparison markup increases its score. Zero markup does not disable distance limits. The route combination is greedy, not a global shortest-route solver.

## Known limitation and next stage

This release changes logging, not procurement decisions. The first demand with an executable supplier anchors the main ware and route. If the combined trip fails its load threshold, the planner **does not retry the whole draft with another main ware or another main supplier**. A nearby factory offering small newly produced quantities can therefore repeatedly anchor an unsuitable large-ship draft, or cause a low-load tail trip, even when another main ware could form a fuller trip.

The next stage will evaluate bounded retries across main wares and suppliers, with a per-round work budget to balance distance preference and cargo utilization. **This remains unresolved in beta.3.** Empty offers and fully reserved quantities are still skipped; this is not unconditional procurement from an empty factory.

## Cancellations, diagnostics and idle ships

- Canceling a trade releases its demand for later reassignment without counting native reservations twice.
- Changing the home sector moves the ship to the new home's dispatch group and search area. An idle ship with no suitable trade returns to the new home. Already queued trades remain intact.
- Check an individual ship's **Information → Behavior** failure message for rejection reasons. This is a behavior diagnostic, not financial transaction history. It reports the last actual rejection, not an exhaustive explanation of all suppliers.
- **Information → Logbook** records one compact report per actual dispatch: settings, normal/tail mode, selected purchases, total load and aggregate rejections. Idle/failure reports have a 10-minute cooldown that price/stock fluctuations cannot reset. Counts and the smallest price/range shortfall use existing checks, without extra searches; they are first-failure observations, not unique supplier counts or proof that all later checks would pass. Native ship log entries also appear in the general logbook.
- For detailed diagnostics, start X4 with **`-scriptlogfiles`**, then enable **Extension Options → Mules-Supply-Mod-OPUS → Debug: detailed disk logging**. The menu uses the public SirNukes Mod Support APIs interface alongside kuertee UI Extensions; Mod Support APIs is needed for the menu, while normal procurement works without it. The switch is off by default and saved per savegame. Detailed candidates go only to **Documents/Egosoft/X4/[user ID]/logs/MulesExtended/debug.txt**. X4 scripts cannot write directly into the mod installation folder. Debug can reduce performance; disable it after reproducing the issue. Upgrading preserves old log entries and queued trades, and removes only obsolete full-report caches.
- Each idle M4 with no active or queued orders returns near its home station or into its home sector, unless the player is flying it. Other busy M4 ships do not delay its return. Returning ships do not need to occupy a trade berth.
- Return flight continues across 30-second dispatch checks; a check with no new job does not restart the route or travel-drive charging. Disabling home-only service still allows other player receivers within the current home's range.
- When a ship has no active or queued orders, leftover cargo is virtually liquidated: cargo is deleted and its average-price value is credited to the player. This is not a station delivery and can include manually loaded cargo.
- M4 follows valid buy offers. It does not rewrite automatic storage allocation, automatic prices or research demand targets.

## Verification and source

**127 script checks, 2 release-package checks, 28 XML schema validations and 3 native patch-selector checks passed.** English diagnostic execution and text-format argument parity are covered by local checks. The retired M4 search loop and its two unused helper scripts have been removed.

The install ZIP is extracted into an isolated test `extensions` directory and checked for correct nesting, metadata, CAT/DAT content, language files and both READMEs. A `SHA256SUMS.txt` release asset verifies the download.

Local XML checks and schema checks cannot establish native-game language rendering, flight, save compatibility or FPS. Existing feedback covers capacity ordering, multiple lots, tiny demands, reassignment after cancellation and the latest return fix; the complete configuration/save matrix still requires gameplay testing. No large-fleet FPS benchmark is available.

```powershell
python tests/checks.py
python tests/test_package.py
python tools/build.py
```

The local build generates `dist/Mules-Supply-Mod-OPUS-v0.1.0-beta.3.zip`; this is the beta.3 public download. Advanced implementation notes and the testing matrix are currently in Chinese: [design](docs/DESIGN.zh-CN.md), [testing](docs/TESTING.zh-CN.md), [changes](docs/CHANGELOG.zh-CN.md).

Derived from [Mules and Warehouses Extended](https://github.com/Misunderstood-Wookiee/Mules-and-Warehouses-Extended), retaining **CC BY 4.0** and upstream attribution. See [LICENCE.md](LICENCE.md) and [ATTRIBUTION.md](ATTRIBUTION.md). This is an independently maintained derivative, not an official upstream or Egosoft release.
