# FutBot Codebase Map (bot.py)

> **Purpose:** Quick-reference index so you never need to grep. All line numbers refer to `bot.py` (the monolith).

---

## 📂 File Structure

| Lines | Section |
|-------|---------|
| 1-185 | **Setup:** Imports, Config, DB Tables, Migrations |
| 186-245 | **Bot Setup & Events:** `on_ready`, `on_message`, `on_command_error` |
| 246-430 | **Help & Misc:** `help`, `about`, `version`, `changelog`, `suggest` |
| 431-486 | **Achievements** |
| 487-906 | **Secret Commands:** England, Brazil, Italy, Germany, Netherlands, France |
| 907-975 | **Card & Player Classes + Helpers** |
| 976-1092 | **Card Weights & Drop Logic** (incl. `NON_DROPPABLE_TYPES`) |
| 1088-1232 | **Auto-Drop, Starter Pack** |
| 1237-1556 | **View Command + Card Details UI** |
| 1557-1638 | **Lookup Edition Picker** (`LookupEditionSelect`, `LookupEditionView`) |
| 1639-1830 | **Lookup (Minted Card)** |
| 1770-2083 | **Daily, Drop** |
| 2084-2234 | **Stats, Titles** |
| 2235-2476 | **Leaderboards** |
| 2477-2746 | **Inventory View** |
| 2747-2935 | **Trade System (Simple 1:1)** |
| 2936-3352 | **Advanced Exchange System (Multi-Card + Coins)** |
| 3353-3956 | **Battle System** |
| 3957-4010 | **Battle Commands** |
| 4011-4158 | **Coins, Sell Command** |
| 4159-4338 | **Multi-Sell Menu** |
| 4339-4726 | **Packs & Shop** |
| 4727-4940 | **Decks: View, Create, Edit** |
| 4941-5156 | **Visual Deck Builder** |
| 5157-5182 | **Economy Helpers** |
| 5183-5306 | **Catalog Viewer** (filters `NON_DROPPABLE_TYPES`) |
| 5307-5441 | **Wishlist** |
| 5442-5710 | **Friends** (`/addfriend`, `/removefriend`, `/friends`, `on_command_completion` notify hook) |
| 5711-5870 | **Trade Up** (`/tradeup`, `TradeUpSelect`, `TradeUpConfirmView`) |
| 5871-6240 | **Transfer Market** (`/list`, `/market`, `/listed`, `/unlist`, expiry task) |
| 6241-6310 | **Admin Commands** |
| 6311-6330 | **Slash Sync & Run** |

---

## 🎮 Commands Index

| Command | Type | Line | Description |
|---------|------|------|-------------|
| `help` | Prefix | 304-311 | Interactive help menu |
| `about` | Prefix | 393-401 | Bot info |
| `version` | Prefix | 404-408 | Version number |
| `changelog` | Prefix | 410-413 | Version history |
| `suggest` | Prefix | 419-430 | Send suggestions |
| `facts` | Prefix | 920-925 | Random football facts |
| `daily` | Hybrid | 1781-1949 | Daily reward with streak |

| `drop` | Prefix | 2010-2075 | Manual card drop |
| `starter` | Prefix | 1207-1232 | Starter pack |
| `view` | Hybrid | 1483-1556 | View card details |
| `lookup` | Hybrid | 1691-1769 | Minted card slab |
| `weight` | Prefix | 1077-1087 | Check card weight |
| `stats` | Prefix | 2153-2190 | Player statistics |
| `set_title` | Prefix | 2216-2234 | Set display title |
| `leaderboard` | Prefix | 2458-2463 | Main leaderboard |
| `richest` | Prefix | 2465-2476 | Coins leaderboard |
| `inventory` | Prefix | 2483-2508 | View cards owned |
| `catalog` | Prefix | 5280-5306 | Browse all cards |
| `wishlist` | Prefix | 5389-5441 | Toggle wishlist (legacy) |
| `wishlists` | Prefix | 5359-5385 | View wishlist |
| `addfriend` | Hybrid | ~5575 | Send a friend request |
| `removefriend` | Hybrid | ~5610 | Remove a friend (both directions) |
| `friends` | Hybrid | ~5625 | View friends list + pending requests button |
| `tradeup` | Hybrid | ~5750 | Trade 5 cards up to a higher rarity |
| `list` | Hybrid | ~5820 | List a card on transfer market |
| `market` | Hybrid | ~5760 | Browse transfer market |
| `listed` | Hybrid | ~5810 | View your active listings |
| `unlist` | Hybrid | ~5830 | Cancel a market listing |
| `trade` | Prefix | 2853-2904 | Simple 1:1 trade |
| `exchange` | Prefix | 3338-3352 | Advanced multi-trade |
| `battle` | Prefix | 3984-3994 | Start battle |
| `decks` | Prefix | 4731-4755 | View all decks |
| `create_deck` | Prefix | 4758-4781 | Create deck |
| `edit_deck` | Prefix | 4784-4827 | Edit deck |
| `view_deck` | Prefix | 4898-4940 | Visual deck lineup |
| `build_deck` | Prefix | 5121-5156 | Visual deck builder |
| `shop` | Prefix | 4373-4379 | View shop |
| `buy` | Prefix | 4382-4408 | Buy pack |
| `packs` | Prefix | 4410-4431 | View owned packs |
| `open` | Prefix | 4509-4535 | Open pack |
| `sell` | Hybrid | 4088-4158 | Sell cards |
| `coins` | Prefix | 5163-5174 | Check balance |
| `give_coins` | Admin | 5446-5467 | Give coins |
| `give_player` | Admin | 5472-5494 | Give card |
| `remove_player` | Admin | 5498-5512 | Remove card |
| `sync` | Admin | 5517-5527 | Sync slash commands |

### 🔐 Secret Commands

| Command | Country | Line |
|---------|---------|------|
| `itscominghome` | 🏴󠁧󠁢󠁥󠁮󠁧󠁿 England | 516-575 |
| `jogabonito` | 🇧🇷 Brazil | 582-641 |
| `pineappleonpizza` | 🇮🇹 Italy | 648-707 |
| `mannschaft` | 🇩🇪 Germany | 715-774 |
| `theflyingdutchmen` | 🇳🇱 Netherlands | 781-840 |
| `blues` | 🇫🇷 France | 847-906 |

---

## 🧩 UI Components (Views & Buttons)

| Class | Purpose | Line |
|-------|---------|------|
| `HelpSelect` | Help menu dropdown | 246-297 |
| `HelpView` | Help container | 299-302 |
| `ChangelogView` | Paginated changelog | 313-350 |
| `CollectButton` | Card collection | 1090-1132 |
| `ViewCardSelect` | Card search dropdown | 1267-1342 |
| `ViewCardSelectView` | View container | 1344-1348 |
| `ToggleWishlistButton` | Add/remove wishlist | 1351-1438 |
| `CardDetailsView` | Card view container | 1440-1452 |
| `LookupEditionSelect` | Edition picker dropdown for /lookup | ~1557 |
| `LookupEditionView` | Edition picker container | ~1600 |
| `DailyView` | Daily reward UI | ~1840 |
| `CollectCardButton` | Daily collect | 2241-2279 |
| `TimedCollectButton` | Timed drop collect | 2091-2135 |
| `DropView` | Drop UI container | 2086-2089 |
| `TitleDropdown` | Title selector | 2193-2208 |
| `TitleDropdownView` | Title container | 2210-2213 |
| `LeaderboardSelect` | LB type dropdown | 2372-2409 |
| `ScopeButton` | Server/Global toggle | 2412-2441 |
| `LeaderboardView` | LB container | 2444-2453 |
| `SortSelect` | Inventory sort | 2511-2553 |
| `FilterModal` | Inventory filter | 2555-2586 |
| `PreviousButton` | Pagination | 2593-2604 |
| `FilterButton` | Open filter modal | 2606-2612 |
| `ResetFilterButton` | Reset filters | 2614-2637 |
| `NextButton` | Pagination | 2639-2650 |
| `InventoryView` | Inventory container | 2654-2746 |
| `TradeView` | Simple trade UI | 2752-2850 |
| `ExchangeSession` | Exchange state | 2948-2960 |
| `ExchangeView` | Exchange container | 3139-3271 |
| `ExAddCardButton` | Add card to exchange | 3275-3287 |
| `ExAddCoinsButton` | Add coins | 3289-3295 |
| `ExClearButton` | Clear offer | 3297-3310 |
| `ExLockButton` | Lock offer | 3312-3319 |
| `ExConfirmButton` | Confirm exchange | 3321-3329 |
| `ExCancelButton` | Cancel exchange | 3331-3336 |
| `Battle` | Battle game class | 3359-3749 |
| `SurrenderButton` | Surrender in battle | 3772-3778 |
| `DrawButton` | Offer draw | 3780-3786 |
| `SurrenderConfirmView` | Confirm surrender | 3788-3802 |
| `SetupView` | Battle setup | 3806-3817 |
| `DeckSelectMenu` | Deck picker | 3819-3845 |
| `ActionView` | Attack/Def/Speed | 3847-3881 |
| `CardSelectView` | Card picker | 3883-3891 |
| `CardDropdown` | Card selector | 3893-3915 |
| `NextRoundView` | Next round button | 3917-3956 |
| `BattleInviteView` | Battle invite | 3961-3981 |
| `ConfirmButton` | Sell confirm | 4042-4058 |
| `DeclineButton` | Sell decline | 4060-4065 |
| `MultiSellSelect` | Multi-sell dropdown | 4162-4198 |
| `MultiSellView` | Multi-sell container | 4200-4286 |
| `MultiSellConfirmButton` | Confirm multi-sell | 4288-4338 |
| `DeckBuilderSelect` | Deck builder dropdown | 4946-4986 |
| `DeckBuilderView` | Deck builder container | 4988-5059 |
| `BuilderPrevButton` | Builder prev | 5063-5068 |
| `BuilderNextButton` | Builder next | 5070-5075 |
| `BuilderSaveButton` | Save deck | 5077-5110 |
| `BuilderCancelButton` | Cancel builder | 5112-5117 |
| `CatalogView` | Catalog container | 5187-5278 |
| `WishlistView` | Wishlist container | 5312-5356 |
| `FriendRequestView` | Accept/Decline buttons for a single request | ~5535 |
| `FriendsListView` | /friends container + 'View Pending' button | ~5560 |
| `TradeUpSelect` | 5-card sacrifice picker | ~5720 |
| `TradeUpConfirmView` | Trade up confirm/cancel | ~5520 |
| `MarketDurationSelect` | Duration picker for /list | ~5680 |
| `MarketConfirmButton` | Confirm listing | ~5500 |
| `MarketCancelButton` | Cancel listing flow | ~5530 |
| `MarketListingView` | /list flow container | ~5540 |
| `MarketEditionSelect` | Edition picker for /list (dupes) | ~5555 |
| `MarketBuyButton` | Buy inline button in /market | ~5690 |
| `MarketBrowseView` | Paginated market browser | ~5720 |

---

## 🔧 Helper Functions

| Function | Purpose | Line |
|----------|---------|------|
| `load_id_list` | Parse .env lists | 31-34 |
| `migrate_db` | DB migrations | 129-173 |
| `global_channel_check` | Channel filter | 187-197 |
| `ensure_player_exists` | Create player | 962-966 |
| `add_card` | Insert card to DB | 968-975 |
| `get_card_by_name` | Fuzzy card search | 978-987 |
| `get_card_by_id` | Get card by ID | 501-509 |
| `add_card_to_inventory` | Add to inventory (duplicates allowed) | 990-1001 |
| `remove_card_from_inventory` | Remove from inv | 4035-4040 |
| `get_player_inventory` | Get user cards | 1011-1019 |
| `fetch_all_cards` | Load all cards | 1021-1024 |
| `weighted_choice` | Random by weight | 1139-1146 |
| `get_card_weight_by_name` | Get card weight | 1050-1073 |
| `increment_card_copies` | +1 copies | 1234-1236 |
| `increment_cards_dropped` | +1 dropped stat | 2138-2149 |
| `increment_cards_sold` | +1 sold stat | 4014-4016 |
| `check_card_ownership` | Check if owned | 4019-4025 |
| `add_coins` | Add to balance | 4028-4033 |
| `deduct_coins` | Remove from balance | 4483-4488 |
| `has_sufficient_coins` | Check balance | 4475-4481 |
| `calculate_card_value` | Sell price | 4068-4085 |
| `get_user_packs` | Get owned packs | 4435-4443 |
| `add_pack_to_user` | Give pack | 4446-4463 |
| `remove_pack_from_user` | Remove pack | 4468-4473 |
| `add_deck` | Create deck | 2907-2927 |
| `get_deck` | Get deck cards | 3997-4010 |
| `generate_lineup_image` | Deck image | 4835-4896 |
| `generate_minted_card` | Minted slab | 1562-1688 |
| `handle_single_drop` | Auto-drop helper | 1151-1184 |
| `card_drop` | Auto-drop task | 1186-1201 |
| `build_leaderboard_embed` | LB embed | 2293-2369 |
| `configure_battle_buttons` | Battle UI helper | 3755-3768 |
| `add_winner_coins` | Battle reward | 5176-5178 |
| `add_loser_coins` | Battle reward | 5180-5182 |
| `_are_friends` | Check if two users are friends | ~5520 |
| `_add_friendship` | Insert bidirectional friendship rows | ~5525 |
| `_send_pending_friend_notification` | Helper used by `on_command_completion` | ~5685 |

---

## 📊 Data Classes

| Class | Purpose | Line |
|-------|---------|------|
| `Card` | Card model | 929-948 |
| `Player` | Player model | 951-960 |

---

## 🗄️ Database Tables

| Table | Purpose | Created At |
|-------|---------|------------|
| `cards` | All cards | 53-68 |
| `decks` | User decks | 70-77 |
| `players` | User profiles + stats | 85-110 |
| `player_achievements` | Earned achievements | 118-127 |
| `wishlists` | User wishlists | (migrated) |
| `user_packs` | Owned packs | (migrated) |
| `market_listings` | Transfer market (active + history) | (migrated, line ~187) |
| `friendships` | Bidirectional friend rows | (migrated, line ~203) |
| `friend_requests` | Pending friend requests + notified flag | (migrated, line ~211) |

---

## 🔑 Config Variables

| Variable | Source | Line |
|----------|--------|------|
| `TOKEN` | .env | 37 |
| `ADMIN_IDS` | .env | 38 |
| `DROP_CHANNEL_IDS` | .env | 39 |
| `ALLOWED_CHANNELS` | .env | 40 |
| `SUGGESTION_CHANNEL_ID` | .env | 41 |
| `NON_DROPPABLE_TYPES` | Hardcoded constant | ~1043 |

---

**Last Updated:** May 2026 · v1.7.1
