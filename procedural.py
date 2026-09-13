import random
import numpy as np

from config import (
    TRAITS_PER_PLAYER_MAX,
    TRAITS_PER_PLAYER_MIN,
    TRAITS_POOL,
)

# Weighted lists for name generation
FIRST_NAMES = {
    "Europe": ["Thomas", "Lucas", "Marc", "Julian", "Oliver", "Harry", "Jack", "Noah", "Leo", "Theo"],
    "Middle_East": ["Mohamed", "Ahmed", "Ali", "Omar", "Zaid", "Hassan", "Youssef", "Ibrahim", "Mustafa", "Karim"],
    "South_America": ["Gabriel", "Enzo", "Mateo", "Thiago", "Diego", "Luis", "Felipe", "Bruno", "Lucas", "Juan"],
}

LAST_NAMES = {
    "Europe": ["Smith", "Jones", "Williams", "Brown", "Taylor", "Davies", "Wilson", "Evans", "Thomas", "Roberts"],
    "Middle_East": ["El-Sayed", "Ghanem", "Mansour", "Salah", "Hassan", "Mahmoud", "Abdullah", "Ebeid", "Fahmy", "Salem"],
    "South_America": ["Silva", "Santos", "Ferreira", "Oliveira", "Pereira", "Rodrigues", "Almeida", "Nascimento", "Lima", "Araujo"],
}

PERSONALITIES = ["Normal", "Leader", "Ambitious", "Loyal", "Glass", "Professional", "Lazy"]

def generate_player_name(region="Europe"):
    first = random.choice(FIRST_NAMES.get(region, FIRST_NAMES["Europe"]))
    last = random.choice(LAST_NAMES.get(region, LAST_NAMES["Europe"]))
    return f"{first} {last}"

def generate_player_stats(tier_level=3, position="MID", region="Europe"):
    """
    Tiers: 1 (Elite), 2 (Professional), 3 (Amateur/Lower)
    """
    # Bell curve mean based on tier
    if tier_level == 1:
        base_mean = 75
    elif tier_level == 2:
        base_mean = 65
    else:
        base_mean = 55
        
    std_dev = 7
    
    # Gaussian distribution
    base_att = random.gauss(base_mean, std_dev)
    base_def = random.gauss(base_mean, std_dev)
    base_pot = random.gauss(base_mean + 10, std_dev)
    
    # Position Bias
    if position == 'FWD':
        attack = base_att * 1.2
        defense = base_def * 0.7
    elif position == 'DEF' or position == 'GK':
        attack = base_att * 0.7
        defense = base_def * 1.2
    else: # MID
        attack = base_att * 1.05
        defense = base_def * 1.05

    # Regional Bias
    if region == "South_America":
        attack *= 1.05
        defense *= 0.95
    elif region == "Middle_East":
        attack *= 0.98
        base_pot *= 1.02 # High potential often found

    # Clamping
    final_attack = max(30, min(99, int(attack)))
    final_defense = max(30, min(99, int(defense)))
    final_potential = max(max(final_attack, final_defense), min(99, int(base_pot)))
    
    # Biometric Data
    if position == "GK":
        height = random.randint(185, 200)
        weight = random.randint(80, 95)
    elif position == "DEF":
        height = random.randint(180, 195)
        weight = random.randint(75, 90)
    else:
        height = random.randint(165, 185)
        weight = random.randint(60, 80)
        
    age = random.randint(16, 35)
    personality = random.choices(PERSONALITIES, weights=[50, 5, 10, 10, 5, 15, 5], k=1)[0]
    
    # Market Value estimation
    val = (final_attack + final_defense) * 1000
    if age < 23: val *= 1.5
    if final_potential > 85: val *= 2
    
    return {
        "attack": final_attack,
        "defense": final_defense,
        "potential": final_potential,
        "age": age,
        "height": height,
        "weight": weight,
        "personality": personality,
        "market_value": int(val),
        "region": region
    }


def generate_player_traits() -> list:
    """Roll a small set of hidden traits for a new player (FM: Traits)."""
    count = random.randint(TRAITS_PER_PLAYER_MIN, TRAITS_PER_PLAYER_MAX)
    return random.sample(TRAITS_POOL, k=count)


def roll_physicals(age: int) -> dict:
    """Base stamina/speed/technique derived from age for procedural players."""
    # Physical peak ~24, technical keeps rising into the late 20s.
    physical_peak = 1.0 - max(0, age - 24) * 0.02
    technical = 1.0 + min(0.2, max(0, age - 24) * 0.015)
    return {
        "stamina": max(30, min(100, int(random.gauss(70, 15) * physical_peak))),
        "speed": max(30, min(100, int(random.gauss(65, 18) * physical_peak))),
        "technique": max(30, min(100, int(random.gauss(55, 18) * technical))),
    }


def generate_region_talent(region: str, scout_level: int) -> dict:
    """Scout discovery: a young high-potential prospect from a region."""
    name = generate_player_name(region)
    pos = random.choices(["GK", "DEF", "MID", "FWD"], weights=[1, 3, 3, 2], k=1)[0]
    tier_level = 1 if scout_level >= 4 else (2 if scout_level >= 2 else 3)
    stats = generate_player_stats(tier_level=tier_level, position=pos, region=region)
    stats["age"] = random.randint(16, 19)
    stats["potential"] = max(stats["potential"], random.randint(80, 96))
    return {"name": name, "position": pos, "stats": stats, "traits": generate_player_traits()}
