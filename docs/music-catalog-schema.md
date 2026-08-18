# Схема данных и связей каталога музыкального стриминга

Модель каталога, в которой у трека есть родительская сущность: она собирает все версии
одного прочтения — студийную, концертную, старую запись, ремастер, перезапись — и связывает
их с каверами других артистов. Спроектирована так, чтобы из неё напрямую выводились точки
входа в слушание, информационная архитектура экранов и разметка событий для CJM.

Оформленная версия документа со схемами: `docs/music-catalog.html`.

---

## 1. Основание модели: три уровня вместо одного «трека»

Слово «трек» означает три разные вещи. Разделив их, получаем и родительскую сущность,
и корректные каверы, и чистую статистику.

| Уровень | Сущность | Что это | Идентификатор |
|---|---|---|---|
| L0 | **Произведение** (`work`) | То, что написали: мелодия и текст. Не звучит. | ISWC |
| L1 | **Трек** (`track`) | Прочтение произведения конкретным артистом. **Родительская сущность.** | — |
| L2 | **Запись** (`recording`) | Конкретная фонограмма. Только она играет. | ISRC |

**Ключевое разделение уровней:**

- Лайк, подписка, «моя музыка», агрегированная популярность → **Трек**.
  Человек любит песню, а не мастеринг 2015 года.
- Плей, длительность, доступность, лицензия → **Запись**.
- Авторские отчисления, «все каверы мира» → **Произведение**.

Без этого разделения одна песня лежит семью строками в поиске, лайк на ремастере не виден
на оригинале, а кавер невозможно отличить от ремикса.

### Пример: «Hurt»

```
L0  Произведение «Hurt» — музыка и текст: Трент Резнор
     ├── L1  Трек: Nine Inch Nails — Hurt          (оригинальное прочтение)
     │        ├── L2  Студийная, 1994 · The Downward Spiral   [is_primary]
     │        └── L2  Концертная, Woodstock '94    → Событие
     └── L1  Трек: Johnny Cash — Hurt              (is_cover_of → трек NIN)
              ├── L2  Студийная, 2002 · American IV           [is_primary]
              └── L2  Ремастер 2015                → remaster_of Запись 2002
```

Кавер — связь **между Треками**. Концертник и ремастер — Записи **внутри одного Трека**.
Разные типы связи на разных уровнях: именно это позволяет показать «все версии» отдельно
от «кто ещё это пел».

---

## 2. Куда ложатся восемь исходных сущностей

Три из восьми — Композитор, Поэт и Музыканты — не таблицы, а **роли**. Это не педантизм:
Меркьюри одновременно артист, композитор, поэт и вокалист, и в модели с отдельными
таблицами он размножится в четыре несинхронизированные записи.

| Исходная сущность | В модели | Что важно |
|---|---|---|
| **Артист** | `artist` | Творческая единица: человек, группа, оркестр, псевдоним, проект. Отдельно от `person` — физлица. |
| **Трек** | `work` → **`track`** → `recording` | Раскладывается на три уровня. `track` — родительская сущность. |
| **Альбом** | `album` → `edition` | Тот же паттерн «родитель → версии»: альбом и его издания. |
| **Сборник** | `album` с `album_type = compilation` | Не отдельная таблица, а тип релиза. |
| **Плейлист** | `playlist` + `station` | Четыре подтипа. Бесконечный поток — это `station`, не плейлист. |
| **Композитор** | `credit(role=composer)` → `work` | Роль на уровне произведения. |
| **Поэт** | `credit(role=lyricist)` → `work` | Там же. Плюс переводчик и автор адаптации. |
| **Музыканты** | `credit(role=performer)` → `recording` | Привязка к **записи**: на концертнике 2003 состав другой, чем в студии 1994. |

**Правило уровня привязки.** Кредит крепится к тому уровню, где его вклад реально существует.
Написал → Произведение. Сыграл, спродюсировал, свёл → Запись. Оформил обложку, издал → Релиз.
Ошибка уровня — самая частая причина, почему потом невозможно построить страницу
«этот барабанщик играл ещё вот здесь».

---

## 3. Шесть семейств вокруг ядра

| Семейство | Сущности | Отношение к ядру |
|---|---|---|
| **Ядро каталога** | `work` · `track` · `recording` | — |
| **Релизы** | `album` · `edition` · `release_track` | **содержат Записи** |
| **Люди и роли** | `person` · `artist` · `credit` · `role` · `artist_membership` · `artist_relation` | кредиты на все три уровня ядра |
| **Курирование** | `playlist` · `playlist_item` · `station` · `chart` · `shelf` | **ссылаются на Записи** |
| **Контекст и смыслы** | `event` · `tour` · `venue` · `session` · `studio` · `label` · `tag` · `lyrics` | описывают Запись |
| **Потребление** | `app_user` · `library_item` · `follow` · `play_event` · `taste_profile` | **лайк → Трек, плей → Запись** |

Асимметрия, которую легко потерять: релизы и плейлисты ссылаются на **Запись** — иначе
сборник не сможет содержать именно концертную версию, а плейлист перестанет
воспроизводиться одинаково. Библиотека и подписки ссылаются на **Трек** и **Артиста**.

---

## 4. Спецификация сущностей

Перечислены значимые поля; `id`, timestamps и служебные поля источника подразумеваются везде.

### 4.1 Ядро каталога

#### Произведение — `work`

Абстрактная композиция. Существует, даже если ни разу не записана.

| Поле | Комментарий |
|---|---|
| `iswc` | международный код произведения |
| `title_canonical`, `alt_titles[]` | каноническое название и варианты/переводы |
| `language_original` | язык оригинального текста |
| `year_written` | год написания |
| `work_type` | `song` · `instrumental` · `classical` · `medley` |
| `parent_work_id` | для частей сюиты и классических циклов |
| `original_track_id` | эталонное прочтение |
| `derived_from_work_id`, `derivation_type` | сэмпл · интерполяция · перевод · аранжировка |
| `is_public_domain` | влияет на UGC и караоке |

#### Трек — `track` (родительская сущность)

Прочтение произведения конкретным артистом. Контейнер всех версий.
Единица, которую пользователь считает «песней».

| Поле | Комментарий |
|---|---|
| `work_id` | → Произведение |
| `primary_artist_id` | → Артист |
| `title_display` | как показывать в интерфейсе |
| `first_release_date` | дата появления прочтения |
| `is_cover_of_track_id` | → Трек, с которого сделан кавер |
| `cover_type` | `cover` · `tribute` · `translation` · `re_recording` |
| `default_recording_id` | вычисляется на рынок, см. §6 |
| `popularity_rollup`, `saves_rollup` | агрегаты по всем версиям; лайки крепятся сюда |
| `versions_count` | драйвер блока «все версии» |

#### Запись — `recording`

Конкретная фонограмма. Единственная сущность, которую можно воспроизвести.

| Поле | Комментарий |
|---|---|
| `track_id` | → Трек |
| `isrc` | международный код записи |
| `version_type` | закрытое перечисление, см. §5 |
| `version_label` | «Live at Wembley, 1986» |
| `duration_ms` | длительность |
| `recorded_at` | **дата записи — отдельно от даты выпуска** |
| `released_at` | дата первой публикации |
| `is_live`, `is_instrumental`, `is_explicit` | быстрые фильтры |
| `language` | язык вокала, может отличаться от произведения |
| `bpm`, `key`, `loudness`, `energy` | для подбора и радио |
| `remix_of_recording_id` | ремикс — производная записи |
| `remaster_of_recording_id` | тот же дубль, новый мастер |
| `event_id`, `session_id`, `studio_id` | → Концерт, Сессия, Студия |
| `catalog_visibility` | `primary` · `secondary` · `hidden` |
| `availability[]` | территория × тариф × окно |
| `audio_qualities[]` | lossless · hi-res · spatial |
| `has_synced_lyrics`, `has_video` | драйверы фич |

### 4.2 Релизы

#### Альбом — `album`

То, что пользователь называет альбомом. Один и тот же паттерн «родитель → версии»:
одна карточка в интерфейсе, несколько изданий под ней.

| Поле | Комментарий |
|---|---|
| `primary_artist_id` | NULL для сборной солянки |
| `album_type` | `album` · `ep` · `single` · **`compilation`** · `live` · `soundtrack` · `mixtape` |
| `is_various_artists` | ключ для сборников |
| `compilation_kind` | `greatest_hits` · `thematic` · `label` · `soundtrack` |
| `first_release_date` | дата оригинального выпуска |
| `default_edition_id` | какое издание показывать |

#### Издание — `edition`

Конкретный выпуск: оригинал, Deluxe, ремастер, юбилейное, региональное.
UPC, лейбл и обложка есть именно у издания.

Поля: `album_id`, `upc`, `edition_label`, `released_at`, `territory`, `label_id`,
`cover_art`, `total_discs`.

#### Трек в издании — `release_track`

Связка «издание ↔ **запись**». Ссылается на Запись, а не на Трек — иначе сборник не сможет
содержать концертную версию, а Deluxe-издание не отличить от оригинала.

Поля: `edition_id`, `recording_id`, `disc_no`, `position`, `is_bonus`, `is_hidden`,
`display_artist` (переопределение для сборников).

> **Сборник vs плейлист.** Оба — упорядоченные коллекции. Различает их **наличие прав
> и издателя**: сборник — релиз с UPC и лейблом, он неизменен, входит в дискографию
> и попадает в чарты альбомов. Плейлист — подборка без прав на выпуск, живой
> и переупорядочиваемый. Слить их в одну таблицу заманчиво, но тогда ломается дискография
> артиста и отчётность по релизам.

### 4.3 Люди и роли

- **`person`** — физлицо: `legal_name`, `sort_name`, `born_at`, `died_at`, `country`, `ipi`.
  Нужен отдельно от Артиста, чтобы Дэймон Албарн связывался и с Blur, и с Gorillaz.
- **`artist`** — творческая единица со страницей в продукте: `name`, `disambiguation`,
  `artist_type` (`person` · `band` · `duo` · `orchestra` · `project` · `alias`),
  `parent_artist_id` (псевдонимы и сайд-проекты), `formed_at`, `disbanded_at`, `country`,
  `is_verified`, `bio`, `images`.
- **`artist_membership`** — состав с датами: `artist_id`, `person_id`, `roles[]`,
  `valid_from`, `valid_to`. Делает возможным честный ответ «кто играл на этой записи».
- **`artist_relation`** — `relation_type`: участник · псевдоним · сайд-проект ·
  коллаборация · преемник · влияние. Топливо для навигации «похожие».
- **`credit`** — полиморфная связь «кто → что сделал → над чем». **Здесь живут композитор,
  поэт и музыканты.** Поля: `person_id` | `artist_id`, `target_type` + `target_id`
  (`work` · `track` · `recording` · `edition`), `role_id`, `instrument`, `is_primary`,
  `is_featured`, `sort_order`.
- **`role`** — справочник с обязательным `applies_to_level`:
  - `writing` → композитор · поэт · аранжировщик · переводчик (уровень `work`)
  - `performance` → вокал · инструмент · дирижёр · фит (уровень `recording`)
  - `production` → продюсер · сведение · мастеринг · звукорежиссёр (уровень `recording`)

Уровень в справочнике не даёт повесить «гитарист» на произведение,
а «композитор» — на конкретный мастеринг.

### 4.4 Курирование

- **`playlist`** — одна таблица, четыре подтипа с разной механикой:
  `playlist_type` = `editorial` · `user` · `algorithmic` · `auto`.
  Плюс `owner_ref`, `visibility` (приватный · по ссылке · публичный), `is_collaborative`,
  `is_dynamic`, `refresh_policy`, `seed_refs[]`, `ruleset` (умные плейлисты),
  `markets[]`, `editorial_slot`.
- **`playlist_item`** — ссылается на **Запись**. Если сослаться на Трек, плейлист однажды
  заиграет ремастером вместо оригинала, и это будет воспринято как баг.
  Поля: `position`, `added_by`, `added_at`, `version_locked`.
- **`station`** — бесконечный поток от сида (`seed_ref`: артист · трек · жанр · вкус),
  фиксированного списка нет.
- **`chart`** — `scope` (мир · страна · город) × `period`.
- **`shelf`** — управляемый блок витрины: `surface`, `rule`, `item_refs[]`, `audience`,
  `slot_priority`. Именно она превращает модель данных в информационную архитектуру
  главного экрана.

### 4.5 Контекст и смыслы

- **`event` · `tour` · `venue`** — концерт: `date`, `venue_id`, `tour_id`, `setlist[]`
  (→ Треки, даже если записи нет).
- **`session` · `studio`** — студийная сессия: `date_range`, `studio_id`, `personnel[]`.
- **`label`** — `name`, `parent_label_id`.
- **`tag` / `tag_assignment`** — единая таксономия: жанр · поджанр · настроение · активность ·
  эпоха · инструмент · тема · язык. Обязательны `target_type` и `source`
  (`editorial` · `ml` · `label` · `user`) — редакторский жанр и ML-настроение нельзя
  показывать одинаково и нельзя чинить одинаково. Плюс `confidence`, `weight`.
- **`lyrics`** — привязан к **Записи**: в концертной версии текст импровизируется,
  в радиоверсии зацензурен, в переводной — другой язык. Поля: `lines[]` с таймкодами,
  `language`, `is_translation`, `provider`, `rights_window`.

### 4.6 Потребление

- **`library_item`** — `user_id` + `ref_type` (`track` · `album` · `playlist` · `artist` ·
  `recording`) + `ref_id`, `added_at`, `source_entry_point`, `pinned_recording_id`
  («мне нравится именно эта версия»).
- **`play_event`** — центральный факт аналитики:
  `user_id`, `session_id`, `recording_id`, **`track_id`**, **`work_id`** (денормализация
  для роллапов), `context_type` + `context_id`, `entry_point`, `surface`,
  `position_in_context`, `started_at`, `ms_played`, `completion_rate`, `is_skip`,
  `skip_at_ms`, `is_repeat`, `reason_start`, `reason_end`, `device`, `is_offline`.

Без денормализации до `work_id` нельзя ответить «сколько всего слушают эту песню
во всех прочтениях».

---

## 5. Версия, кавер, ремикс, ремастер

Четыре слова, которые в разговоре путают, а в модели они лежат на трёх разных уровнях.
Различие определяется одним вопросом: **что переиспользовали.**

| Явление | Что переиспользовано | Уровень связи | Поле |
|---|---|---|---|
| **Версия** — концертная, демо, акустика, радиоверсия | то же прочтение, другое исполнение | внутри Трека | `recording.track_id` |
| **Ремастер** | тот самый дубль, новая обработка | внутри Трека | `remaster_of_recording_id` |
| **Ремикс** | исходный мастер, новая аранжировка поверх | внутри Трека | `remix_of_recording_id` |
| **Перезапись** («Taylor's Version») | то же прочтение, заново записанное ради прав | внутри Трека | `version_type = re_recording` |
| **Кавер** | только произведение, исполнение новое | **между Треками** | `track.is_cover_of_track_id` |
| **Сэмпл, интерполяция** | фрагмент чужого произведения или записи | **между Произведениями** | `work.derived_from_work_id` |

### Перечисление `version_type`

Заводить закрытым списком с самого начала — открытая строка «версия» превращается
в помойку из тысяч уникальных значений за первый же год работы с лейблами.

| Группа | Значения | Показывать в дискографии |
|---|---|---|
| Основные | `studio` `single_edit` `radio_edit` | да, как основную |
| Концертные | `live` `live_session` `unplugged` `rehearsal` | да, отдельным блоком |
| Архивные | `demo` `alt_take` `outtake` `early_version` | в блоке «редкое» |
| Переработки | `remaster` `re_recording` `remix` `extended` `club_mix` | ремастер вместо оригинала, остальное блоком |
| Служебные | `instrumental` `acapella` `karaoke` `clean` `censored` | скрыто, по фильтру |
| Ситуативные | `sped_up` `slowed` `soundtrack_version` `orchestral` `acoustic` | по решению редакции |

**Зачем нужен `catalog_visibility`.** Ускоренные версии, инструменталы и караоке нужны
в каталоге, но не должны засорять «Топ-треки артиста». Поле со значениями
`primary` / `secondary` / `hidden` отделяет «что в каталоге» от «что на витрине».
Одно поле снимает больше жалоб на качество каталога, чем любой алгоритм ранжирования.

---

## 6. Какую версию запускает Play

Раз у Трека много Записей, а кнопка одна, нужно явное правило разрешения — кодом
в одном месте, а не по-своему на каждом экране.

1. **Версия задана контекстом** — играем её. Из плейлиста, издания альбома или карточки
   версии выбор уже сделан пользователем или куратором; переопределять нельзя.
2. **Иначе берём `default_recording` для рынка.** Материализованное поле, а не вычисление
   на лету — иначе не воспроизвести аналитику.
3. **Фильтр доступности** — лицензия на территорию, тариф и текущее окно.
   Недоступные отсеиваются до всякого ранжирования.
4. **Флаг редакции `is_primary_for_track`** — если проставлен и версия доступна, побеждает.
5. **Иначе эвристика:** актуальный ремастер выше оригинала, студийная выше концертной,
   полная версия выше радиоверсии, при прочих равных — популярность за 28 дней.
6. **Фолбэк** — любая доступная запись, **с логированием**: систематические фолбэки
   означают дыру в лицензировании, и это должно быть видно.

Следствие для интерфейса: если у трека больше одной видимой версии, кнопка Play должна
сопровождаться доступом к выбору — иначе пользователь, искавший концертник, уйдёт
с ощущением, что его нет в сервисе.

---

## 7. Точки входа в слушание

Каждая точка входа — пара «сущность, на которой стоит человек» + «что произойдёт по Play».
Таблица одновременно задаёт контракт плеера и словарь `entry_point` в аналитике.

| Точка входа | Сущность | Что играет | Порядок очереди |
|---|---|---|---|
| Страница артиста | `artist` | `default_recording` топ-треков | по популярности |
| Родительский трек | `track` | `default_recording` | дальше — радио от трека |
| **Блок «все версии»** | `track` → `recording[]` | выбранная версия | по типу и дате записи |
| **Блок «кто ещё это пел»** | `work` → `track[]` | default другого прочтения | оригинал первым, дальше популярность |
| Альбом или издание | `album` · `edition` | записи издания | порядок треклиста |
| Сборник | `album(compilation)` | записи сборника | порядок треклиста |
| Плейлист | `playlist` | записи из элементов | позиция или шафл |
| Станция и радио | `station` | поток от сида | бесконечный, ML |
| Чарт | `chart` | записи позиций | по позиции |
| **Кредит** — композитор, поэт, музыкант | `credit` → `work` · `recording` | автосборка «всё, где участвовал» | хронология или популярность |
| **Концерт, тур, сессия** | `event` · `session` | записи события | сетлист |
| Текст песни | `lyrics` | запись с этого таймкода | — |
| Тег: жанр, настроение, эпоха | `tag` | станция по тегу | ML |
| Библиотека и история | `library_item` | сохранённое, с учётом pinned-версии | дата добавления |
| Внешняя ссылка и шеринг | любая сущность | по её правилу | по её правилу |

**Проектное следствие.** Четыре точки входа — «все версии», «кто ещё это пел», «кредит»
и «концерт» — **не существуют в плоской модели каталога**. Они появляются ровно потому,
что произведение, трек и запись разделены, а кредиты и контекст вынесены в отдельные
сущности. Это и есть продуктовая отдача от усложнения схемы: четыре новых способа
провалиться вглубь каталога вместо перебора плейлистов.

---

## 8. Экраны и информационная архитектура

Прямая проекция модели на ИА. Если блок на экране не выводится из связи в схеме — либо
в схеме дырка, либо блок держится на ручном контенте и не масштабируется.

| Экран | Ядро | Блоки и откуда они берутся |
|---|---|---|
| **Артист** | `artist` | Топ-треки `track[]` · Дискография `album[]` с фильтром по типу · Синглы · **Концертные записи** `recording(is_live)` · Появления `credit` на чужих записях · Состав `artist_membership` · Похожие `artist_relation` · Радио |
| **Трек** (родительский) | `track` | Play → default версия · **Все версии** `recording[]`, сгруппированные по `version_type` · **Каверы** `work → track[]` · Авторы `credit(work)` · Музыканты `credit(recording)` · Текст · На каких релизах звучит · В каких плейлистах |
| **Версия** | `recording` | Где и когда записано `event` · `session` · `studio` · Состав именно этой записи · Другие версии трека · Ремиксы |
| **Произведение** | `work` | Оригинал · Все прочтения всех артистов · Авторы · Что сэмплировало · Хронология. Может быть скрыто в MVP, но связь нужна сразу |
| **Альбом** | `album` | Треклист `release_track` · Переключатель изданий `edition[]` · Кредиты релиза · Лейбл |
| **Сборник** | `album(compilation)` | Треклист с обязательным показом артиста в строке · Составитель · Переход к альбому-источнику каждой записи |
| **Плейлист** | `playlist` | Элементы · Автор и обложка · Для алгоритмических — объяснение «почему это здесь» из `seed_refs` · Похожие |
| **Кредит** | `person` · `role` | Страница композитора, поэта, продюсера, сессионного музыканта: всё, где человек отметился, с разбивкой по ролям |
| **Главная** | `shelf[]` | Полки как сущности, а не как вёрстка: правило наполнения, аудитория, приоритет слота — редактируемо без релиза приложения |

---

## 9. CJM и разметка событий

Шесть стадий пути к музыке. Для каждой — сущность, которая её обслуживает, и событие,
без которого стадию нельзя измерить. Разметка вводится вместе со схемой, а не после запуска.

| Стадия | Что делает человек | Сущности | Событие и метрика |
|---|---|---|---|
| **Повод** | «хочу что-то под настроение / под задачу» | `tag` · `shelf` · `station` | показ полки → клик. *CTR полки, доля сессий, начатых не с поиска* |
| **Ориентирование** | ищет знакомое или разбирается, кто это | `artist` · `work` · `track` · `chart` | переход на карточку. *Глубина навигации до первого плея* |
| **Выбор версии** | «мне нужна та, концертная» | `recording` · `version_type` | открытие «все версии», смена версии. *Доля плеев не по умолчанию — прямой замер спроса на родительскую сущность* |
| **Погружение** | читает текст, смотрит, кто играл, идёт по кредитам | `lyrics` · `credit` · `event` · `album` | раскрытие блока, переход по кредиту. *Доля сессий с уходом вглубь* |
| **Присвоение** | лайкает, добавляет, подписывается | `library_item` · `playlist` · `follow` | сохранение с `source_entry_point`. *Конверсия точки входа в сохранение* |
| **Возврат** | приходит снова за тем же и рядом стоящим | история · `taste_profile` · `station` | повторный плей. *Доля возвратов к треку через 7 дней* |

### Три среза, которые даёт трёхуровневая модель

- **Спрос на версии.** Отношение плеев не-дефолтной версии к общему числу по треку.
  Показывает, где родительская сущность реально работает: у каких артистов и жанров люди
  осознанно ищут концертник или демо.
- **Жизнь произведения.** Плеи, свёрнутые до `work_id`: видно, что кавер приносит
  слушателей оригиналу, и можно измерить эффект попадания песни в фильм или тренд.
- **Продуктивность точек входа.** Разложение плеев по `entry_point` с конверсией
  в сохранение. Единственный способ честно сравнить «страницу артиста» с «полкой
  на главной» и понять, что стоит развивать.

---

## 10. Решения, которые лучше принять сейчас

Каждое дёшево заложить на этапе схемы и очень дорого менять после запуска.

| Вопрос | Рекомендация | Цена ошибки |
|---|---|---|
| Лайк — на трек или на версию? | На **Трек**, с необязательным `pinned_recording_id` | Лайк на ремастере не виден на оригинале; библиотека выглядит дублированной |
| Показывать ли Произведение в интерфейсе? | Связь завести сразу, экран — позже | Ретроспективно связать каверы почти невозможно: нужен ручной разбор каталога |
| Дубли одной фонограммы с разными ISRC от разных дистрибьюторов | Слой сопоставления и склейки с ручной модерацией — с первого дня | Самая частая жалоба на качество каталога и главный источник размытой статистики |
| Классика: кто «артист» — композитор, дирижёр, оркестр? | Композитор — `credit(work)`, витрина — отдельная логика для academic-жанров | Классический каталог становится неюзабельным; модель должна его допускать |
| Медли, мэшапы, попурри | Связь Запись ↔ Произведение как многие-ко-многим | Неверные авторские отчисления и потерянные каверы |
| Ускоренные и замедленные версии | Отдельные Записи с `catalog_visibility = secondary` | Либо засоряют дискографию, либо теряется трафик из соцсетей |
| Кто хозяин `default_recording` — редакция или алгоритм? | Алгоритм по умолчанию, редакторский флаг перебивает | Без ручного перебивания нельзя исправить очевидные ошибки, без алгоритма — не масштабируется |

---

## 11. Что резать в MVP

Правило разреза: **связи закладываются все сразу, экраны — по мере надобности.**
Связь, не заведённую с самого начала, потом придётся восстанавливать ручным разбором каталога.

**Обязательно в первой версии**

- Три уровня ядра — даже если Произведение нигде не показывается
- `version_type`, `catalog_visibility`, `default_recording_id`
- `track.is_cover_of_track_id`
- Альбом → Издание → Трек в издании, со ссылкой на Запись
- `credit` с уровнем привязки и `role` с ограничением уровня
- `person` отдельно от `artist`
- `entry_point` и `context_type` в `play_event`

**Можно отложить**

- Экраны Произведения и Кредита — связи есть, витрина позже
- `event`, `tour`, `venue`, `session` — на старте достаточно текстового `version_label`
- Станции по тегам, умные плейлисты по правилам
- Полки как редактируемые сущности — первая витрина может быть захардкожена
- Совместные плейлисты, переводы текстов

**Чего не делать никогда**

- Не хранить версию строкой в названии трека — «Song (Live 1994 Remastered)» не парсится обратно
- Не заводить отдельные таблицы для композитора, поэта и музыканта
- Не ссылаться из плейлиста и релиза на Трек вместо Записи
- Не делать сборник подвидом плейлиста

---

## 12. Приложение: набросок DDL

Сокращённо, PostgreSQL. Индексы, партиционирование событий и служебные поля опущены.

```sql
-- L0 ------------------------------------------------------------
CREATE TABLE work (
  id              uuid PRIMARY KEY,
  iswc            text UNIQUE,
  title_canonical text NOT NULL,
  language_original text,
  year_written    int,
  work_type       text NOT NULL,          -- song | instrumental | classical | medley
  parent_work_id  uuid REFERENCES work(id),
  original_track_id uuid,                 -- FK на track, ставится отложенно
  derived_from_work_id uuid REFERENCES work(id),
  derivation_type text,                   -- sample | interpolation | translation | arrangement
  is_public_domain boolean NOT NULL DEFAULT false
);

-- L1: родительская сущность трека --------------------------------
CREATE TABLE track (
  id                 uuid PRIMARY KEY,
  work_id            uuid NOT NULL REFERENCES work(id),
  primary_artist_id  uuid NOT NULL REFERENCES artist(id),
  title_display      text NOT NULL,
  first_release_date date,
  is_cover_of_track_id uuid REFERENCES track(id),
  cover_type         text,                -- cover | tribute | translation | re_recording
  default_recording_id uuid,              -- материализуется на рынок
  popularity_rollup  int  NOT NULL DEFAULT 0,
  saves_rollup       int  NOT NULL DEFAULT 0,
  UNIQUE (work_id, primary_artist_id, title_display)
);

-- L2: версии ------------------------------------------------------
CREATE TABLE recording (
  id            uuid PRIMARY KEY,
  track_id      uuid NOT NULL REFERENCES track(id),
  isrc          text UNIQUE,
  version_type  text NOT NULL,            -- studio | live | demo | remaster | remix | ...
  version_label text,                     -- 'Live at Wembley, 1986'
  duration_ms   int  NOT NULL,
  recorded_at   date,
  released_at   date,
  is_live       boolean NOT NULL DEFAULT false,
  is_explicit   boolean NOT NULL DEFAULT false,
  is_instrumental boolean NOT NULL DEFAULT false,
  language      text,
  bpm           numeric, music_key text, loudness numeric,
  remix_of_recording_id    uuid REFERENCES recording(id),
  remaster_of_recording_id uuid REFERENCES recording(id),
  event_id      uuid REFERENCES event(id),
  session_id    uuid REFERENCES session(id),
  is_primary_for_track boolean NOT NULL DEFAULT false,
  catalog_visibility text NOT NULL DEFAULT 'secondary'  -- primary | secondary | hidden
);

CREATE TABLE recording_availability (
  recording_id uuid NOT NULL REFERENCES recording(id),
  territory    char(2) NOT NULL,
  tier         text    NOT NULL,          -- free | premium | hi-res
  valid_from   date, valid_to date,
  PRIMARY KEY (recording_id, territory, tier, valid_from)
);

-- Релизы ----------------------------------------------------------
CREATE TABLE album (
  id uuid PRIMARY KEY,
  primary_artist_id uuid REFERENCES artist(id),   -- NULL для сборной солянки
  title text NOT NULL,
  album_type text NOT NULL,               -- album | ep | single | compilation | live | soundtrack
  is_various_artists boolean NOT NULL DEFAULT false,
  compilation_kind text,                  -- greatest_hits | thematic | label | soundtrack
  first_release_date date,
  default_edition_id uuid
);

CREATE TABLE edition (
  id uuid PRIMARY KEY,
  album_id uuid NOT NULL REFERENCES album(id),
  upc text UNIQUE,
  edition_label text,                     -- 'Deluxe', '10th Anniversary Remaster'
  released_at date,
  territory char(2),
  label_id uuid REFERENCES label(id),
  total_discs int NOT NULL DEFAULT 1
);

CREATE TABLE release_track (              -- ссылается на ЗАПИСЬ, не на трек
  edition_id   uuid NOT NULL REFERENCES edition(id),
  recording_id uuid NOT NULL REFERENCES recording(id),
  disc_no  int NOT NULL DEFAULT 1,
  position int NOT NULL,
  is_bonus boolean NOT NULL DEFAULT false,
  display_artist text,                    -- переопределение для сборников
  PRIMARY KEY (edition_id, disc_no, position)
);

-- Люди и роли -----------------------------------------------------
CREATE TABLE person (
  id uuid PRIMARY KEY, legal_name text NOT NULL, sort_name text,
  born_at date, died_at date, country char(2), ipi text
);

CREATE TABLE artist (
  id uuid PRIMARY KEY, name text NOT NULL, disambiguation text,
  artist_type text NOT NULL,              -- person | band | duo | orchestra | project | alias
  parent_artist_id uuid REFERENCES artist(id),
  formed_at date, disbanded_at date, country char(2),
  is_verified boolean NOT NULL DEFAULT false
);

CREATE TABLE artist_membership (
  artist_id uuid NOT NULL REFERENCES artist(id),
  person_id uuid NOT NULL REFERENCES person(id),
  roles text[] NOT NULL,
  valid_from date, valid_to date,
  PRIMARY KEY (artist_id, person_id, valid_from)
);

CREATE TABLE role (
  id uuid PRIMARY KEY, name text NOT NULL,
  category text NOT NULL,                 -- writing | performance | production | technical
  applies_to_level text NOT NULL          -- work | track | recording | edition
);

-- Композитор, поэт и музыканты живут здесь ------------------------
CREATE TABLE credit (
  id uuid PRIMARY KEY,
  person_id uuid REFERENCES person(id),
  artist_id uuid REFERENCES artist(id),
  target_type text NOT NULL,              -- work | track | recording | edition
  target_id   uuid NOT NULL,
  role_id     uuid NOT NULL REFERENCES role(id),
  instrument  text,
  is_primary  boolean NOT NULL DEFAULT false,
  is_featured boolean NOT NULL DEFAULT false,
  sort_order  int NOT NULL DEFAULT 0,
  CHECK (num_nonnulls(person_id, artist_id) = 1)
);

-- Курирование -----------------------------------------------------
CREATE TABLE playlist (
  id uuid PRIMARY KEY,
  playlist_type text NOT NULL,            -- editorial | user | algorithmic | auto
  owner_user_id uuid REFERENCES app_user(id),
  title text NOT NULL, description text, cover_url text,
  visibility text NOT NULL DEFAULT 'private',
  is_collaborative boolean NOT NULL DEFAULT false,
  is_dynamic boolean NOT NULL DEFAULT false,
  refresh_policy text, ruleset jsonb, seed_refs jsonb,
  markets char(2)[]
);

CREATE TABLE playlist_item (              -- тоже на ЗАПИСЬ
  playlist_id  uuid NOT NULL REFERENCES playlist(id),
  recording_id uuid NOT NULL REFERENCES recording(id),
  position int NOT NULL,
  added_by uuid REFERENCES app_user(id),
  added_at timestamptz NOT NULL DEFAULT now(),
  version_locked boolean NOT NULL DEFAULT true,
  PRIMARY KEY (playlist_id, position)
);

-- Потребление -----------------------------------------------------
CREATE TABLE library_item (
  user_id uuid NOT NULL REFERENCES app_user(id),
  ref_type text NOT NULL,                 -- track | album | playlist | artist | recording
  ref_id   uuid NOT NULL,
  added_at timestamptz NOT NULL DEFAULT now(),
  source_entry_point text,                -- откуда сохранил — вход в CJM
  pinned_recording_id uuid REFERENCES recording(id),
  PRIMARY KEY (user_id, ref_type, ref_id)
);

CREATE TABLE play_event (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL,
  session_id uuid NOT NULL,
  recording_id uuid NOT NULL REFERENCES recording(id),
  track_id uuid NOT NULL,                 -- денормализация для роллапов
  work_id  uuid NOT NULL,
  context_type text NOT NULL,             -- playlist | album | artist | station | search | library
  context_id   uuid,
  entry_point  text NOT NULL,             -- словарь из §7
  surface      text,
  position_in_context int,
  started_at timestamptz NOT NULL,
  ms_played  int NOT NULL,
  completion_rate numeric,
  is_skip boolean NOT NULL DEFAULT false,
  skip_at_ms int,
  is_repeat boolean NOT NULL DEFAULT false,
  reason_start text,                      -- click | autoplay | shuffle | continue
  reason_end   text,
  device text, is_offline boolean NOT NULL DEFAULT false
);
```
