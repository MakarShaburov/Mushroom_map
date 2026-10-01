def ru_plural(n, forms):
    """Выбирает нужную форму слова по числу n для русского языка.

    forms — кортеж из трёх форм: (1 штука, 2-4 штуки, 5+ штук),
    например ('оценка', 'оценки', 'оценок').
    """
    n = abs(int(n)) % 100
    n1 = n % 10
    if 10 < n < 20:
        return forms[2]
    if 1 < n1 < 5:
        return forms[1]
    if n1 == 1:
        return forms[0]
    return forms[2]
