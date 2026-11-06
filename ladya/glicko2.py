"""Рейтинг Glicko-2 (Mark Glickman, http://www.glicko.net/glicko/glicko2.pdf).

Как на lichess: у каждого игрока отдельный рейтинг по каждому контролю времени (пуля, блиц, рапид,
классика) и в задачах. Старт 1500, RD (неуверенность) 350 — первые партии сильно двигают рейтинг,
потом всё меньше. Рейтинг пересчитывается после каждой партии (период = одна партия).
"""
import math

START, START_RD, START_VOL = 1500.0, 350.0, 0.06
TAU = 0.5
SCALE = 173.7178
MIN_RD, MAX_RD = 45.0, 350.0
PROVISIONAL_RD = 110            # RD выше — рейтинг «предварительный» (показываем со знаком «?»)


def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi * phi / math.pi ** 2)


def _e(mu, mu_j, phi_j):
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def update(rating, rd, vol, opp_rating, opp_rd, score):
    """Новый (rating, rd, vol) после одной партии. score: 1 — победа, 0.5 — ничья, 0 — поражение."""
    mu, phi = (rating - 1500) / SCALE, rd / SCALE
    mu_j, phi_j = (opp_rating - 1500) / SCALE, opp_rd / SCALE
    g_j, e_j = _g(phi_j), _e(mu, mu_j, phi_j)
    v = 1 / (g_j ** 2 * e_j * (1 - e_j))
    delta = v * g_j * (score - e_j)

    # новая волатильность (итерационный алгоритм Illinois из статьи)
    a = math.log(vol ** 2)

    def f(x):
        ex = math.exp(x)
        return ex * (delta ** 2 - phi ** 2 - v - ex) / (2 * (phi ** 2 + v + ex) ** 2) - (x - a) / TAU ** 2

    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU
    fA, fB = f(A), f(B)
    for _ in range(100):
        if abs(B - A) <= 1e-6:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2
        B, fB = C, fC
    new_vol = math.exp(A / 2)

    phi_star = math.sqrt(phi ** 2 + new_vol ** 2)
    new_phi = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
    new_mu = mu + new_phi ** 2 * g_j * (score - e_j)
    new_rd = min(MAX_RD, max(MIN_RD, new_phi * SCALE))
    return new_mu * SCALE + 1500, new_rd, new_vol


def decay_rd(rd, vol, days):
    """RD растёт, пока игрок не играет (примерно один «период» в сутки)."""
    phi = rd / SCALE
    for _ in range(min(int(days), 365)):
        phi = math.sqrt(phi ** 2 + vol ** 2)
    return min(MAX_RD, phi * SCALE)
