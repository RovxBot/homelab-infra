# Individual Progression (WotLK) - Flow Chart

Source of truth: `mod-individual-progression` code + SQL in your configured module repo.

## Full progression flow

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"Trebuchet MS, Verdana, sans-serif","fontSize":"12px","lineColor":"#5b6470","primaryTextColor":"#1a1612","tertiaryColor":"#d4c3a1","clusterBkg":"#d4c3a1","clusterBorder":"#7b6640"}}}%%
flowchart TD
    S0[State 0 START]

    %% =========================
    %% VANILLA ATTUNEMENT BRANCHES
    %% =========================
    subgraph VATT[Vanilla Attunement and Prereq Chains]
      direction LR
      subgraph MCATT[Molten Core]
        direction TB
        MCAQ[Accept 7848 Attunement to the Core]
        MCAO[7848 objective:\nLoot Core Fragment in BRD]
        MCAT[7848 turn-in:\nReturn to Lothos Riftwaker]
        MCA2[MC attuned shortcut unlocked:\nLothos teleport to Molten Core]
        MCAQ --> MCAO --> MCAT --> MCA2

        MC6804Q[Accept 6804]
        MC6804O[6804 objective:\nComplete Duke Hydraxis prereq tasks]
        MC6804T[6804 turn-in:\nReturn to Duke Hydraxis]
        MC6805Q[Accept 6805]
        MC6805O[6805 objective:\nComplete Duke Hydraxis prereq tasks]
        MC6805T[6805 turn-in:\nReturn to Duke Hydraxis]
        MC6821Q[Accept 6821 Eye of the Emberseer]
        MC6821O[6821 objective:\nLoot the Eye of the Emberseer in UBRS]
        MC6821T[6821 turn-in:\nReturn to Duke Hydraxis]
        MC6822Q[Accept 6822 The Molten Core]
        MC6822O[6822 objective:\nEnter Molten Core and complete quest tasks]
        MC6822T[6822 turn-in:\nReturn to Duke Hydraxis]
        MC6823Q[Accept 6823 Agent of Hydraxis]
        MC6823O[6823 objective:\nContinue Hydraxis field assignment]
        MC6823T[6823 turn-in:\nReturn to Duke Hydraxis]
        MC6824Q[Accept 6824 Hands of the Enemy]
        MC6824O[6824 objective:\nCollect required raid drops for Hydraxis]
        MC6824T[6824 turn-in:\nReturn to Duke Hydraxis]
        MC5[Aqual Quintessence gossip unlock]
        MC6[Hydraxian rep gate\nHonored Aqual / Revered Eternal]
        MC7[Douse Firelord runes\nManualRuneHandling=1]
        MC8[Majordomo flow unlocked]

        MC6804Q --> MC6804O --> MC6804T
        MC6805Q --> MC6805O --> MC6805T
        MC6804T --> MC6821Q
        MC6805T --> MC6821Q
        MC6821Q --> MC6821O --> MC6821T --> MC6822Q --> MC6822O --> MC6822T
        MC6822T --> MC6823Q --> MC6823O --> MC6823T --> MC6824Q --> MC6824O --> MC6824T
        MC6824T --> MC5
        MC6824T --> MC6 --> MC7 --> MC8
      end

      subgraph ONYATT[Onyxia]
        direction TB
        OA4183Q[Accept 4183 The True Masters]
        OA4183O[4183 objective:\nFollow The True Masters lead and report]
        OA4183T[4183 turn-in:\nTurn in to chain contact]
        OA4184Q[Accept 4184]
        OA4184O[4184 objective:\nContinue The True Masters investigation]
        OA4184T[4184 turn-in:\nTurn in to chain contact]
        OA4185Q[Accept 4185]
        OA4185O[4185 objective:\nContinue The True Masters investigation]
        OA4185T[4185 turn-in:\nTurn in to chain contact]
        OA4186Q[Accept 4186]
        OA4186O[4186 objective:\nFinish The True Masters sequence]
        OA4186T[4186 turn-in:\nTurn in to chain contact]
        OA4322Q[Accept 4322 Jail Break]
        OA4322O[4322 objective:\nComplete Jail Break escort in BRD]
        OA4322T[4322 turn-in:\nReturn to Marshal Windsor chain]
        OA6403Q[Accept 6403 The Great Masquerade]
        OA6403O[6403 objective:\nComplete Great Masquerade event in Stormwind]
        OA6403T[6403 turn-in:\nReport completion for amulet reward]
        OA2[Alliance reward:\nDrakefire Amulet 16309]

        OA4183Q --> OA4183O --> OA4183T --> OA4184Q --> OA4184O --> OA4184T
        OA4184T --> OA4185Q --> OA4185O --> OA4185T --> OA4186Q --> OA4186O --> OA4186T
        OA4186T --> OA4322Q --> OA4322O --> OA4322T --> OA6403Q --> OA6403O --> OA6403T --> OA2

        OH7490Q[Accept 7490 Warlord's Command]
        OH7490O[7490 objective:\nAnswer Warlord's Command and report]
        OH7490T[7490 turn-in:\nTurn in to chain commander]
        OH7491Q[Accept 7491 Dragonkin Menace]
        OH7491O[7491 objective:\nComplete Dragonkin Menace kill tasks]
        OH7491T[7491 turn-in:\nTurn in to chain commander]
        OH7493Q[Accept 7493 The Eastern Kingdoms]
        OH7493O[7493 objective:\nDeliver reports across Eastern Kingdoms contacts]
        OH7493T[7493 turn-in:\nTurn in to chain contact]
        OH7495Q[Accept 7495 Warlord's Command]
        OH7495O[7495 objective:\nContinue Horde command briefing chain]
        OH7495T[7495 turn-in:\nTurn in to chain commander]
        OH7496Q[Accept 7496 Message to Rexxar]
        OH7496O[7496 objective:\nDeliver message to Rexxar]
        OH7496T[7496 turn-in:\nTurn in to Rexxar]
        OH7497Q[Accept 7497]
        OH7497O[7497 objective:\nComplete Rexxar-assigned field objective]
        OH7497T[7497 turn-in:\nReturn to Rexxar]
        OH7498Q[Accept 7498]
        OH7498O[7498 objective:\nComplete Rexxar-assigned follow-up objective]
        OH7498T[7498 turn-in:\nReturn to Rexxar]
        OH7499Q[Accept 7499 Onyxia's Lair]
        OH7499O[7499 objective:\nComplete final Onyxia briefing objective]
        OH7499T[7499 turn-in:\nTurn in final report for amulet reward]
        OH3[Horde reward:\nDrakefire Amulet 16309]

        OH7490Q --> OH7490O --> OH7490T --> OH7491Q --> OH7491O --> OH7491T
        OH7491T --> OH7493Q --> OH7493O --> OH7493T --> OH7495Q --> OH7495O --> OH7495T
        OH7495T --> OH7496Q --> OH7496O --> OH7496T --> OH7497Q --> OH7497O --> OH7497T
        OH7497T --> OH7498Q --> OH7498O --> OH7498T --> OH7499Q --> OH7499O --> OH7499T --> OH3

        O1[Must carry Drakefire Amulet 16309\nfor Vanilla Onyxia entry]
        OA2 --> O1
        OH3 --> O1
      end

      subgraph BWLATT[Blackwing Lair]
        direction TB
        B0Q[Accept 7761 Blackhand's Command]
        B0O[7761 objective:\nTouch orb behind General Drakkisath in UBRS]
        B0T[7761 completion:\nUse orb attunement confirmation]
        B2[BWL attuned shortcut unlocked:\norb at Blackrock Mountain]
        B0Q --> B0O --> B0T --> B2
      end

      subgraph AQSCARABATT[AQ Gates and Scepter Attunement]
        direction TB
        GONG{Scarab Gong path}
        GONG --> GWEPRE[Shared prereq:\nComplete War Effort first]
        GWEPRE --> WESETQ[Accept faction collection quest set\nAlliance 8492..8528 / Horde 8532..8615]
        WESETQ --> WESETO[Collection objectives:\nFinish every listed faction supply quest]
        WESETO --> WESETT[Collection turn-ins:\nTurn in all completed supply quests]

        WESETT --> WE2Q[Accept Complete the War Effort\n108850 or 108855]
        WE2Q --> WE2O[War Effort objective:\nTurn in 1000 tokens 21436 or 21438]
        WE2O --> WE2T[War Effort turn-in:\nSubmit to faction war effort NPC]

        WE2T --> GPOSTWE{Choose gong route after War Effort}
        GPOSTWE --> SQ8286Q[Accept 8286 What Tomorrow Brings\nBaristolth of the Shifting Sands,\nCenarion Hold Silithus]
        SQ8286Q --> SQ8286O[8286 objective:\nTravel to Anachronos in Tanaris\nand continue the chain]
        SQ8286O --> SQ8286T[8286 turn-in:\nAnachronos at the Caverns of Time]

        SQ8286T --> SQ8288Q[Accept 8288 Only One May Rise]
        SQ8288Q --> SQ8288O[8288 objective:\nDefeat Broodlord Lashlayer in BWL\nand recover his head]
        SQ8288O --> SQ8288T[8288 turn-in:\nReturn to Baristolth in Silithus]

        SQ8288T --> SQ8301Q[Accept 8301 The Path of the Righteous]

        SQ8301Q --> SQ8301P{8301 multi-objective split}
        SQ8301P --> SQ8301OA[8301 objective part:\nRecover Red Shard line]
        SQ8301P --> SQ8301OB[8301 objective part:\nRecover Blue Shard line]
        SQ8301P --> SQ8301OC[8301 objective part:\nRecover Green Shard line]
        SQ8301OA --> SQ8301O[8301 objective complete:\nAll three shard lines recovered]
        SQ8301OB --> SQ8301O
        SQ8301OC --> SQ8301O
        SQ8301O --> SQ8301T[8301 turn-in:\nReturn completed shard progress]

        SQ8301T --> SQ8555Q[Accept 8555 The Charge of the Dragonflights]
        SQ8555Q --> SQ8555O[8555 objective:\nSpeak with Anachronos]
        SQ8555O --> SQ8555T[8555 turn-in:\nAnachronos, Caverns of Time]

        SQ8555T --> S3FORK{Three Shard Routes in Parallel}

        %% BLUE SHARD ROUTE
        S3FORK --> SQ8575Q[Accept 8575 Azuregos's Magical Ledger]
        SQ8575Q --> SQ8575O[8575 objective:\nRecover Azuregos's Magical Ledger]
        SQ8575O --> SQ8575T[8575 turn-in:\nNarain Soothfancy, Steamwheedle Port]
        SQ8575T --> SQ8576Q[Accept 8576 Translating the Ledger]
        SQ8576Q --> SQ8576O[8576 objective:\nNeed 3 item lines complete:\nScrying Goggles + 500 Pound Chicken +\nDraconic for Dummies Vol II]

        SQ8576Q --> SQBSTART{Pick up 3 quests at same time}
        SQBSTART --> SQ8577Q[Accept Stewvul, Ex-B.F.F.]
        SQBSTART --> SQBCHIQ[Accept Never Ask Me About My Business]
        SQBSTART --> SQ8597Q[Accept Draconic for Dummies]

        SQ8577Q --> SQ8577O[Stewvul objective:\nDeliver to crate near Greymane Wall]
        SQ8577O --> SQ8577T[Stewvul turn-in]
        SQ8577T --> SQ8578Q[Accept Scrying Goggles? No Problem!]
        SQ8578Q --> SQ8578O[Objective:\nLoot Narain's Scrying Goggles in MC]
        SQ8578O --> SQ8578T[Turn-in goggles to Narain]

        SQBCHIQ --> SQBCHIO[Objective:\nComplete Dirge/Lord Lakmaeran/\nChimaerok Tenderloin and materials chain]
        SQBCHIO --> SQBCHIT[Turn-in and obtain 500 Pound Chicken]

        SQ8597Q --> SQ8597O[Objective:\nAcquire Draconic for Dummies Vol II route item]
        SQ8597O --> SQ8597T[Turn-in Draconic for Dummies]
        SQ8597T --> SQ8598Q[Accept 8598 rAnS0m]
        SQ8598Q --> SQ8598O[8598 objective:\nBring ransom note to Narain]
        SQ8598O --> SQ8598T[8598 turn-in:\nNarain Soothfancy]
        SQ8598T --> SQ8599Q[Accept 8599 Love Song for Narain]
        SQ8599Q --> SQ8599O[8599 objective:\nComplete Love Song handoff path]
        SQ8599O --> SQ8599T[8599 turn-in]
        SQ8599T --> SQ8606Q[Accept 8606 Decoy!]
        SQ8606Q --> SQ8606O[8606 objective:\nUse Narain kit and defeat Number Two]
        SQ8606O --> SQ8606T[8606 turn-in]
        SQ8606T --> SQ8620Q[Accept 8620 The Only Prescription]
        SQ8620Q --> SQ8620O[8620 objective:\nCollect all Draconic chapters + binding]
        SQ8620O --> SQ8620T[8620 turn-in]

        SQ8578T --> SQBMERGE[Blue route merge:\nall three item lines complete]
        SQBCHIT --> SQBMERGE
        SQ8620T --> SQBMERGE
        SQBMERGE --> SQ8576T[8576 turn-in:\nNarain completes translation]
        SQ8576T --> SQ8728Q[Accept 8728 The Good News and The Bad News]
        SQ8728Q --> SQ8728O[8728 objective:\nGather bars, gems, and elementium ore]
        SQ8728O --> SQ8728T[8728 turn-in:\nNarain Soothfancy]
        SQ8728T --> SQWRATHQ[Accept The Wrath of Neptulon]
        SQWRATHQ --> SQWRATHO[Wrath objective:\nUse buoy in Azshara and kill Maws]
        SQWRATHO --> SQWRATHT[Wrath turn-in:\nReturn Blue Shard to Anachronos]
        SQWRATHT --> SBLUE[Blue Shard complete]

        %% RED SHARD ROUTE
        S3FORK --> SREDQ[Accept Nefarius's Corruption\nfrom Vaelastrasz in BWL]
        SREDQ --> SREDO[Objective:\nKill Nefarian within timed window]
        SREDO --> SREDT[Turn-in:\nReturn Red Shard to Anachronos]
        SREDT --> SRED[Red Shard complete]

        %% GREEN SHARD ROUTE
        S3FORK --> SGREENQ1[Accept Eranikus, Tyrant of the Dream]
        SGREENQ1 --> SGREENO1[Objective:\nTravel to Forest Wisp, then to Remulos]
        SGREENO1 --> SGREENT1[Turn-in to Remulos]
        SGREENT1 --> SGREENQ2[Accept The Nightmare's Corruption]
        SGREENQ2 --> SGREENO2[Objective:\nCollect corruption fragments at\nAshenvale, Duskwood, Feralas, Hinterlands portals]
        SGREENO2 --> SGREENT2[Turn-in to Remulos]
        SGREENT2 --> SGREENQ3[Accept The Nightmare Manifests]
        SGREENQ3 --> SGREENO3[Objective:\nDefend Nighthaven event and cleanse Eranikus]
        SGREENO3 --> SGREENT3[Turn-in and bring Green Shard to Anachronos]
        SGREENT3 --> SGREEN[Green Shard complete]

        SBLUE --> SSHARDMERGE{All 3 shards complete}
        SRED --> SSHARDMERGE
        SGREEN --> SSHARDMERGE
        SSHARDMERGE --> G8742Q[Accept 8742 Might of Kalimdor]
        G8742Q --> G8742O[8742 objective:\nComplete pre-gong final event requirements]
        G8742O --> G8742T[8742 turn-in:\nAnachronos at Scarab Wall Silithus]
        G8742T --> G8743Q[Accept 8743 Bang a Gong]
        G8743Q --> G8743O[8743 objective:\nRing the Scarab Gong with Scepter of the\nShifting Sands during opening event]
        G8743O --> G8743T[8743 turn-in:\nComplete at The Scarab Gong, Silithus]

        GPOSTWE --> G108743Q[Accept 108743 Simply Bang a Gong]
        G108743Q --> G108743O[108743 objective:\nWar Effort complete + have Scarab Gong Mallet 9240]
        G108743O --> G108743T[108743 turn-in:\nComplete with gong NPC]
      end

      subgraph NAXX40ATT[Naxx40]
        direction TB
        NCHOICE{Choose one Naxx40 attunement path}

        subgraph NAXXPATH1[Path 1]
          direction TB
          N9121Q[Accept 9121 Naxx40 attunement]
          N9121O[9121 objective:\nComplete attunement requirements]
          N9121T[9121 turn-in:\nReturn to Argent Dawn attunement NPC]
          N9121Q --> N9121O --> N9121T
        end

        subgraph NAXXPATH2[Path 2]
          direction TB
          N9122Q[Accept 9122 Naxx40 attunement]
          N9122O[9122 objective:\nComplete attunement requirements]
          N9122T[9122 turn-in:\nReturn to Argent Dawn attunement NPC]
          N9122Q --> N9122O --> N9122T
        end

        subgraph NAXXPATH3[Path 3]
          direction TB
          N9123Q[Accept 9123 Naxx40 attunement]
          N9123O[9123 objective:\nComplete attunement requirements]
          N9123T[9123 turn-in:\nReturn to Argent Dawn attunement NPC]
          N9123Q --> N9123O --> N9123T
        end

        NCHOICE --> N9121Q
        NCHOICE --> N9122Q
        NCHOICE --> N9123Q
        N9121T --> N0
        N9122T --> N0
        N9123T --> N0
        N0[Naxx40 attunement complete]
        N1[Optional config: RequireNaxxStrathEntrance]
        N0 --> N1
      end
    end

    %% =========================
    %% MAIN PROGRESSION SPINE
    %% =========================
    S0 --> MCA2
    S0 --> MC8
    MCA2 --> RAG[Kill Ragnaros]
    MC8 --> RAG
    RAG --> S1[State 1 MOLTEN_CORE]

    S1 --> O1
    O1 --> ONY[Kill Onyxia]
    ONY --> S2[State 2 ONYXIA]

    S2 --> B2
    B2 --> NEF[Kill Nefarian]
    NEF --> S3[State 3 BLACKWING_LAIR]

    S3 --> GONG
    G8743T --> S4[State 4 PRE_AQ]
    G108743T --> S4

    %% AQ WAR EFFORT CHAIN
    S4 --> COLQ[Accept Colossus quest set\n108745 108746 108747]
    COLQ --> COLO[Colossus objectives:\nComplete Zora, Regal, and Ashi colossus tasks]
    COLO --> COLT[Colossus turn-ins:\nTurn in 108745, 108746, and 108747]

    COLT --> CADQ[Accept 108744 Chaos and Destruction]
    CADQ --> CADO[108744 objective:\nKill 15818 x1, 15743 x3, 15758 x12]
    CADO --> CADT[108744 turn-in:\nReturn to quest NPC]
    CADT --> S5[State 5 AQ_WAR]

    S5 --> CTHUN[Kill CThun]
    CTHUN --> S6[State 6 AQ]

    S6 --> NCHOICE
    N0 --> KT40[Kill KelThuzad Naxx40]
    KT40 --> S7[State 7 NAXX40]

    S7 --> BRQ[Accept 10259 Into the Breach]
    BRQ --> BRO[10259 objective:\nComplete Into the Breach tasks in Hellfire]
    BRO --> BRT[10259 turn-in:\nReturn to Honor Hold/Thrallmar contact]
    BRT --> S8[State 8 PRE_TBC\nOutland open, level 60 cap removed]

    %% =========================
    %% TBC ATTUNEMENT BRANCHES
    %% =========================
    subgraph TATT[TBC Attunement Chains]
      direction LR
      subgraph TKATT[The Eye TK]
        direction TB
        TK0[Trials of the Naaru start]
        TKMQ[Accept 10884 Trial of the Naaru: Mercy]
        TKMO[10884 objective:\nComplete Trial of the Naaru: Mercy requirements]
        TKMT[10884 turn-in:\nReturn to A'dal]
        TKSQ[Accept 10885 Trial of the Naaru: Strength]
        TKSO[10885 objective:\nComplete Trial of the Naaru: Strength requirements]
        TKST[10885 turn-in:\nReturn to A'dal]
        TKTQ[Accept 10886 Trial of the Naaru: Tenacity]
        TKTO[10886 objective:\nComplete Trial of the Naaru: Tenacity requirements]
        TKTT[10886 turn-in:\nReturn to A'dal]
        TK1Q[Accept 10888 Trial of the Naaru: Magtheridon]
        TK1O[10888 objective:\nKill Magtheridon]
        TK1T[10888 turn-in:\nReturn to A'dal for key reward]
        TK2[Reward from 10888:\nTempest Key 31704]
        TK0 --> TKMQ --> TKMO --> TKMT
        TK0 --> TKSQ --> TKSO --> TKST
        TK0 --> TKTQ --> TKTO --> TKTT
        TKMT --> TK1Q
        TKST --> TK1Q
        TKTT --> TK1Q
        TK1Q --> TK1O --> TK1T --> TK2
      end

      subgraph SSCATT[Serpentshrine Cavern]
        direction TB
        SSCQ[Accept 10901 The Cudgel of Kar'desh]
        SSCO[10901 objective:\nComplete Cudgel of Kar'desh attunement requirements]
        SSCT[10901 turn-in:\nReturn to attunement quest giver]
        SSC2[SSC attuned complete]
        SSCQ --> SSCO --> SSCT --> SSC2
      end

      subgraph HYJATT[Mount Hyjal]
        direction TB
        HY0Q[Accept 10445 The Vials of Eternity]
        HY1A[Kill Lady Vashj in SSC\nfor Vashj's Vial Remnant 29906]
        HY1B[Kill Kael'thas in The Eye\nfor Kael's Vial Remnant 29905]
        HY1[10445 objective complete:\nObtain both vial remnants]
        HY0T[10445 turn-in:\nReturn both vials to Soridormi]
        HY2[Hyjal attuned complete]
        HY0Q --> HY1A
        HY0Q --> HY1B
        HY1A --> HY1
        HY1B --> HY1
        HY1 --> HY0T --> HY2
      end

      subgraph BTATT[Black Temple]
        direction TB
        BT10683Q[Accept 10683 Tablets of Baa'ri]
        BT10683O[10683 objective:\nCollect Tablets of Baa'ri]
        BT10683T[10683 turn-in:\nReturn to Akama chain NPC]
        BT10684Q[Accept 10684 Oronu the Elder]
        BT10684O[10684 objective:\nDefeat Oronu the Elder and recover required item]
        BT10684T[10684 turn-in:\nReturn to Akama chain NPC]
        BT10685Q[Accept 10685 The Ashtongue Corruptors]
        BT10685O[10685 objective:\nKill Ashtongue Corruptors and gather drops]
        BT10685T[10685 turn-in:\nReturn to Akama chain NPC]
        BT10686Q[Accept 10686 The Warden's Cage]
        BT10686O[10686 objective:\nComplete The Warden's Cage event]
        BT10686T[10686 turn-in:\nReturn to Akama chain NPC]

        BTCIPHERQ[Accept Cipher of Damnation / Akama quest set]
        BTCIPHERO[Cipher/Akama objectives:\nComplete the required Shadowmoon quest sequence]
        BTCIPHERT[Cipher/Akama turn-ins:\nTurn in each chain step to continue]

        BT10949Q[Accept 10949 Entry into the Black Temple]
        BT10949O[10949 objective:\nComplete Entry into the Black Temple tasks]
        BT10949T[10949 turn-in:\nReturn to Akama chain NPC]
        BT10985Q[Accept 10985 A Distraction for Akama]
        BT10985O[10985 objective:\nComplete A Distraction for Akama event]
        BT10985T[10985 turn-in:\nReturn to Akama chain NPC]
        BT3[Obtain Medallion of Karabor 32649]
        BT4[Optional upgrade:\nBlessed Medallion 32757]

        BT10683Q --> BT10683O --> BT10683T --> BT10684Q --> BT10684O --> BT10684T
        BT10684T --> BT10685Q --> BT10685O --> BT10685T --> BT10686Q --> BT10686O --> BT10686T
        BT10686T --> BTCIPHERQ --> BTCIPHERO --> BTCIPHERT
        BTCIPHERT --> BT10949Q --> BT10949O --> BT10949T --> BT10985Q --> BT10985O --> BT10985T
        BT10985T --> BT3 --> BT4
      end

      TK2 --> HY1
      SSC2 --> HY1
      HY2 --> BTCIPHERQ
    end

    %% TBC tier progression
    S8 --> MALCH[Kill Prince Malchezaar]
    MALCH --> S9[State 9 TBC_TIER_1]

    S9 --> TK0
    S9 --> SSCQ
    TK2 --> KAEL[Kill Kaelthas]
    SSC2 --> KAEL
    KAEL --> S10[State 10 TBC_TIER_2]

    S10 --> HY0Q
    S10 --> BT3
    HY2 --> ILLI[Kill Illidan]
    BT3 --> ILLI
    ILLI --> S11[State 11 TBC_TIER_3]

    S11 --> ZJ[Kill Zuljin]
    ZJ --> S12[State 12 TBC_TIER_4\nQuelDanas/Sunwell open]

    S12 --> KJ[Kill Kiljaeden]
    KJ --> S13[State 13 TBC_TIER_5\nNorthrend open, level 70 cap removed]

    %% WotLK tier progression
    S13 --> KTW[Kill KelThuzad WotLK Naxx]
    KTW --> S14[State 14 WOTLK_TIER_1]
    S14 --> YOGG[Kill YoggSaron]
    YOGG --> S15[State 15 WOTLK_TIER_2]
    S15 --> ANUB[Kill Anubarak ToC]
    ANUB --> S16[State 16 WOTLK_TIER_3]
    S16 --> LK[Kill Lich King]
    LK --> S17[State 17 WOTLK_TIER_4]
    S17 --> HALION[Kill Halion]
    HALION --> S18[State 18 WOTLK_TIER_5 END]

    %% =========================
    %% LEGEND
    %% =========================
    subgraph LEG[Legend]
      direction LR
      LQ[Quest Accept]
      LO[Quest Objectives]
      LT[Quest Hand-in]
      LB[Boss Kill]
      LS[Progression State]
      LG[Gate/Requirement]
      LR[Reward/Attuned]
    end

    %% =========================
    %% VISUAL STYLES
    %% =========================
    classDef quest fill:#c7b28a,stroke:#6f5a37,stroke-width:1px,color:#1b1712
    classDef objective fill:#bca071,stroke:#7a5a1e,stroke-width:1px,color:#1b1712
    classDef handin fill:#9aae82,stroke:#3f5e2f,stroke-width:1px,color:#121812
    classDef boss fill:#8b5a4d,stroke:#5c241a,stroke-width:1.5px,color:#f7ece6
    classDef state fill:#7f8c8d,stroke:#3a434a,stroke-width:1.5px,color:#f8fafc
    classDef gate fill:#9b8f7a,stroke:#4f4638,stroke-width:1px,color:#161514
    classDef reward fill:#87a8a7,stroke:#2f5f63,stroke-width:1.5px,color:#0f1718

    classDef state_vanilla fill:#8f6f46,stroke:#5f4629,stroke-width:1.6px,color:#f8f0e1
    classDef state_tbc fill:#4b6b4b,stroke:#294329,stroke-width:1.6px,color:#eff9ea
    classDef state_wotlk fill:#5e7f9b,stroke:#2d465a,stroke-width:1.6px,color:#f1f7fd
    classDef boss_vanilla fill:#7a4f35,stroke:#4a2d1c,stroke-width:1.6px,color:#fff2e8
    classDef boss_tbc fill:#3f5a3f,stroke:#203420,stroke-width:1.6px,color:#eff9ea
    classDef boss_wotlk fill:#466884,stroke:#1f3f56,stroke-width:1.6px,color:#f3f9ff

    class LQ quest
    class LO objective
    class LT handin
    class LB boss
    class LS state
    class LG gate
    class LR reward

    class S0,S1,S2,S3,S4,S5,S6,S7 state_vanilla
    class S8,S9,S10,S11,S12,S13 state_tbc
    class S14,S15,S16,S17,S18 state_wotlk
    class RAG,ONY,NEF,CTHUN,KT40 boss_vanilla
    class MALCH,KAEL,ILLI,ZJ,KJ boss_tbc
    class KTW,YOGG,ANUB,LK,HALION boss_wotlk
    class GONG,GWEPRE,GPOSTWE,NCHOICE,SQ8301P,S3FORK,SQBSTART,SQBMERGE,SSHARDMERGE gate
    class O1,MC5,MC6,MC7,MC8,B2,N0,N1,SSC2,HY2 gate
    class OA2,OH3,MCA2,TK2,BT3,BT4,SBLUE,SRED,SGREEN reward

    class MCAQ,MC6804Q,MC6805Q,MC6821Q,MC6822Q,MC6823Q,MC6824Q,OA4183Q,OA4184Q,OA4185Q,OA4186Q,OA4322Q,OA6403Q,OH7490Q,OH7491Q,OH7493Q,OH7495Q,OH7496Q,OH7497Q,OH7498Q,OH7499Q,B0Q,N9121Q,N9122Q,N9123Q,SQ8286Q,SQ8288Q,SQ8301Q,SQ8555Q,SQ8575Q,SQ8576Q,SQ8577Q,SQ8578Q,SQ8597Q,SQ8598Q,SQ8599Q,SQ8606Q,SQ8620Q,SQ8728Q,SQBCHIQ,SQWRATHQ,SREDQ,SGREENQ1,SGREENQ2,SGREENQ3,G8742Q,G8743Q,G108743Q,WESETQ,WE2Q,COLQ,CADQ,BRQ,TKMQ,TKSQ,TKTQ,TK1Q,SSCQ,HY0Q,BT10683Q,BT10684Q,BT10685Q,BT10686Q,BTCIPHERQ,BT10949Q,BT10985Q quest
    class MCAO,MC6804O,MC6805O,MC6821O,MC6822O,MC6823O,MC6824O,OA4183O,OA4184O,OA4185O,OA4186O,OA4322O,OA6403O,OH7490O,OH7491O,OH7493O,OH7495O,OH7496O,OH7497O,OH7498O,OH7499O,B0O,N9121O,N9122O,N9123O,SQ8286O,SQ8288O,SQ8301O,SQ8301OA,SQ8301OB,SQ8301OC,SQ8555O,SQ8575O,SQ8576O,SQ8577O,SQ8578O,SQ8597O,SQ8598O,SQ8599O,SQ8606O,SQ8620O,SQ8728O,SQBCHIO,SQWRATHO,SREDO,SGREENO1,SGREENO2,SGREENO3,G8742O,G8743O,G108743O,WESETO,WE2O,COLO,CADO,BRO,TKMO,TKSO,TKTO,TK1O,SSCO,HY1A,HY1B,HY1,BT10683O,BT10684O,BT10685O,BT10686O,BTCIPHERO,BT10949O,BT10985O objective
    class MCAT,MC6804T,MC6805T,MC6821T,MC6822T,MC6823T,MC6824T,OA4183T,OA4184T,OA4185T,OA4186T,OA4322T,OA6403T,OH7490T,OH7491T,OH7493T,OH7495T,OH7496T,OH7497T,OH7498T,OH7499T,B0T,N9121T,N9122T,N9123T,SQ8286T,SQ8288T,SQ8301T,SQ8555T,SQ8575T,SQ8576T,SQ8577T,SQ8578T,SQ8597T,SQ8598T,SQ8599T,SQ8606T,SQ8620T,SQ8728T,SQBCHIT,SQWRATHT,SREDT,SGREENT1,SGREENT2,SGREENT3,G8742T,G8743T,G108743T,WESETT,WE2T,COLT,CADT,BRT,TKMT,TKST,TKTT,TK1T,SSCT,HY0T,BT10683T,BT10684T,BT10685T,BT10686T,BTCIPHERT,BT10949T,BT10985T handin

    style VATT fill:#d4c3a1,stroke:#7b6640,stroke-width:1.5px
    style TATT fill:#9ead8a,stroke:#4f6a40,stroke-width:1.5px
    style LEG fill:#b8c6d2,stroke:#4b6478,stroke-width:1.2px
    style MCATT fill:#dccaa7,stroke:#816846,stroke-width:1px
    style ONYATT fill:#dccaa7,stroke:#816846,stroke-width:1px
    style BWLATT fill:#dccaa7,stroke:#816846,stroke-width:1px
    style AQSCARABATT fill:#d8c49d,stroke:#7e6641,stroke-width:1px
    style NAXX40ATT fill:#d4bf98,stroke:#7a6544,stroke-width:1px
    style TKATT fill:#a8b893,stroke:#4c6b3f,stroke-width:1px
    style SSCATT fill:#a3b38f,stroke:#47623a,stroke-width:1px
    style HYJATT fill:#9caf88,stroke:#426036,stroke-width:1px
    style BTATT fill:#93a880,stroke:#3f5a33,stroke-width:1px
```

## Gate key (instance/zone hard blocks)

- `BWL` requires state `1`.
- `ZG` requires `IndividualProgression.RequiredZulGurubProgression` (current `3`).
- `AQ20/AQ40` require state `4`.
- `Outland` requires state `8` (except BE/Draenei start zones).
- `Northrend` requires state `13`.
- `Ulduar` requires state `14`.
- `ToC/Trial of the Champion` requires state `15`.
- `ICC/Forge of Souls` requires state `16`.
- `Ruby Sanctum` requires state `17`.

## Scarab Gong route note

- In this module, there are two valid routes to `PRE_AQ`:
- Legacy route: full `Scepter of the Shifting Sands` chain is modeled as non-linear (Blue/Red/Green shard routes in parallel with merge before `8742 -> 8743`) and is shown after War Effort completion.
- Alternate route: `108743 Simply Bang a Gong!` after War Effort completion (`108850` or `108855`) and with `Mallet of Zul'Farrak (9240)`.

## TBC attunement checks (in addition to progression state)

- `The Eye` chain: `10884/10885/10886 -> 10888 -> Tempest Key (31704)`.
- `Serpentshrine Cavern` chain: `The Cudgel of Kar'desh (10901)` completion.
- `Mount Hyjal` chain: `The Vials of Eternity (10445)` with vial remnants from `Lady Vashj` and `Kael'thas`.
- `Black Temple` chain: Akama line starts `10683 -> 10684 -> 10685 -> 10686`, continues through BT access chain (`10949 -> 10985` in this module data), then requires `Medallion of Karabor (32649)` or `Blessed Medallion of Karabor (32757)` for entry.

## Raid attunement matrix

This is the practical per-raid view, combining progression gates and extra checks.

| Raid | Progression gate | Extra attunement/entry requirements |
|---|---|---|
| Molten Core | none from state machine | Classic chain includes `Attunement to the Core (7848)` for the Lothos teleport shortcut. For Ragnaros progression in your setup, manual rune dousing path is also required (Hydraxian chain + Quintessence access + douse runes) because `MoltenCore.ManualRuneHandling=1`. |
| Onyxia (Vanilla mode) | none from state machine | Must have `Drakefire Amulet (16309)` and be level `>=50` and `<=70`. |
| Blackwing Lair | state `1` (`PROGRESSION_MOLTEN_CORE`) | Classic attunement chain: `Blackhand's Command (7761)` then touching the UBRS orb behind Drakkisath to unlock orb entry shortcut. |
| AQ20 / AQ40 | state `4` (`PROGRESSION_PRE_AQ`) | Must complete Scarab Gong path (8743 or 108743) before entry unlock. |
| Naxx40 (Vanilla mode) | effectively state path (`AQ -> NAXX40`) | For pre-TBC context, requires one Naxx attunement quest completed: `9121` or `9122` or `9123`. Optional config can also require first entry via old Stratholme entrance (`RequireNaxxStrathEntrance`). |
| Karazhan | state `8` (`PROGRESSION_PRE_TBC`) | No extra attunement check in module teleport gate. |
| Gruul / Magtheridon | state `8` (`PROGRESSION_PRE_TBC`) | No extra item/quest attunement check in module teleport gate. |
| SSC | state `9` (`PROGRESSION_TBC_TIER_1`) | Must complete `The Cudgel of Kar'desh (10901)` chain. |
| The Eye (TK) | state `9` (`PROGRESSION_TBC_TIER_1`) | Must complete `10884/10885/10886 -> 10888` and have `Tempest Key (31704)`. |
| Hyjal | state `10` (`PROGRESSION_TBC_TIER_2`) | Must complete `The Vials of Eternity (10445)` (vials from `Lady Vashj` + `Kael'thas`). |
| Black Temple | state `10` (`PROGRESSION_TBC_TIER_2`) | Must have `Medallion of Karabor (32649)` or `Blessed Medallion of Karabor (32757)` from the Akama BT chain; flow now shows TK/SSC -> Hyjal -> BT dependency path. |
| Zul'Aman | state `11` (`PROGRESSION_TBC_TIER_3`) | No extra attunement check beyond state gate. |
| Sunwell | state `12` (`PROGRESSION_TBC_TIER_4`) | No extra item/quest attunement check beyond state gate. |
| WotLK Naxx / Ulduar / ToC / ICC / RS | states `13+` chain | No additional attunement items/quests in module gate logic; primarily state-gated by tier progression. |

## Current config assumptions used by this chart

- `IndividualProgression.MoltenCore.ManualRuneHandling = 1`
- `IndividualProgression.MoltenCore.AqualEssenceCooldownReduction = 60`
- `IndividualProgression.RequiredZulGurubProgression = 3`
- `IndividualProgression.EnforceGroupRules = 1`
- `IndividualProgression.ProgressionLimit = 0`
- `IndividualProgression.StartingProgression = 0`
