# Промпт vision-прохода

## Проектное решение, которое здесь главное

Модель НЕ отвечает на вопрос «ценный ли это набор». Она отвечает только на
вопросы о наблюдаемой геометрии и материале. Сопоставление наблюдений с
моделями делает детерминированный код по `markers.yaml`.

Почему так. Если спросить «это редкий довоенный Берёзовский?», модель
подтвердит с уверенным тоном на любом наборе похожего силуэта — сходство
высокое, а различающий признак (три кольца под короной против одного)
легко проглатывается в пользу общего впечатления. Разделение
«восприятие отдельно, вывод отдельно» убирает этот режим отказа: модель
может ошибиться в счёте колец, но не может выдумать вердикт.

Практическое следствие: любой ответ поля `unclear` — это НЕ провал, а
рабочий выход. Он превращается в конкретный вопрос продавцу.

## Промпт

```
Перед тобой фотографии из объявления о продаже шахмат. Опиши ТОЛЬКО то,
что видно. Не оценивай ценность, редкость, подлинность и возраст.
Не угадывай модель или производителя.

Если признак не виден на фото — ставь "unclear". Ставить "unclear" лучше,
чем предполагать. Ответь строго этим JSON, без пояснений:

{
  "material": "wood|bone|ivory_like|porcelain|metal|plastic|stone|mixed|unclear",
  "king": {
    "collars_under_crown": "0|1|2|3|more|unclear",
    "crown_shape": "coronet|cross|sphere|other|unclear"
  },
  "bishop": {
    "collars_under_mitre": "0|1|2|3|more|unclear",
    "mitre_slit": "yes|no|unclear"
  },
  "knight": {
    "chest": "straight|v_shaped|unclear",
    "mane_style": "carved_ridges|smooth|ornate|unclear",
    "view_available": "profile|front|angled|none"
  },
  "base": {
    "profile": "wide_conical|narrow_disc|disc_pedestal|flat|unclear",
    "underside_visible": "yes|no",
    "felt_or_baize": "coarse_wool|smooth_modern|none|unclear",
    "stamp_or_mark_visible": "yes|no|unclear",
    "stamp_text_if_readable": "<текст или null>"
  },
  "weighting_evidence": "visible_plug|no_plug|unclear",
  "wear": {
    "pattern": "uneven|uniform|none_visible|unclear",
    "surface_cracks": "yes|no|unclear",
    "finish": "matte_aged|glossy_modern|unclear"
  },
  "completeness": {
    "pieces_countable": "yes|no",
    "pieces_counted": <число или null>
  },
  "box": {
    "present": "yes|no|unclear",
    "era_look": "old|modern|unclear",
    "label_visible": "yes|no|unclear"
  },
  "photo_context": "casual_home|outdoor|studio_white|unclear",
  "scale_reference_present": "yes|no",
  "missing_shots": ["перечисли ракурсы, которых не хватает для определения"]
}
```

## Что происходит дальше

1. `score.py` сопоставляет JSON с `markers.yaml`.
   Пример правила для довоенного Берёзовского: попадание засчитывается,
   если `collars_under_crown == 3` И `knight.chest == straight`, и
   снимается, если `weighting_evidence == no_plug`.
2. Поле `photo_context == studio_white` понижает лот: это магазин.
3. `missing_shots` собирается в готовое сообщение продавцу.

## Шаблон сообщения продавцу

Решающих фото в объявлении почти никогда нет — это нормально и это точка,
где шортлист превращается в решение. Текст намеренно скучный: любой намёк
на то, что вещь может быть ценной, переписывает цену.

> Здравствуйте! Интересуют шахматы. Можно пару доп. фото, чтобы понять
> состояние: {список из missing_shots}. И подскажите, комплект полный,
> сколов нет? Спасибо.

Не писать: «это довоенный набор?», «это Мордовия?», «клеймо есть?».

## Граница метода

Никакой фотоанализ не даёт окончательной атрибуции. Он даёт ранжирование
«кого спросить и что именно спросить». Окончательно различают физически:
способ утяжеления, фактура сукна, столярка коробки, линии Шрегера на
кости. Планируйте осмотр или возврат, а не покупку по фото.
