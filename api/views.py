from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import math
import requests
import datetime

# ------------------ Constantes Científicamente Validadas ------------------

NASA_API_KEY = "dmSkYu2tXVXacdToqJfgInzr4qgOPP1VYdlUoZvK"
NASA_NEO_URL = "https://api.nasa.gov/neo/rest/v1/feed"

# Constantes físicas fundamentales
JOULES_PER_MEGATON = 4.184e15
EARTH_GRAVITY = 9.81  # m/s²
AIR_DENSITY_SEA_LEVEL = 1.225  # kg/m³
SPEED_OF_SOUND = 343  # m/s

# Factores de escala para visualización REALISTA
THERMAL_SCALING_FACTOR = 0.6
BLAST_SCALING_FACTOR = 0.8
FIREBALL_SCALING_FACTOR = 0.7

# ------------------ Modelos Científicos Basados en Referencias ------------------

def _energy_megatons(energia_joules):
    """Conversión exacta de joules a megatones de TNT"""
    return energia_joules / JOULES_PER_MEGATON

def _calculate_kinetic_energy(diametro, densidad, velocidad):
    """
    Calcula energía cinética con corrección de unidades
    """
    if velocidad > 1000:
        velocidad_ms = velocidad
    else:
        velocidad_ms = velocidad * 1000
    
    radio = diametro / 2.0
    volumen = (4.0/3.0) * math.pi * (radio**3)
    masa = densidad * volumen
    
    if velocidad_ms > 30000:
        gamma = 1 / math.sqrt(1 - (velocidad_ms**2 / (299792458**2)))
        energia = (gamma - 1) * masa * (299792458**2)
    else:
        energia = 0.5 * masa * (velocidad_ms**2)
    
    return masa, energia, velocidad_ms

def _crater_metrics(diametro_proy_m, energia_megatons, angulo_impacto, densidad_proy, target_type):
    """
    Calcula dimensiones del cráter usando el modelo de Schmidt-Holsapple
    """
    if energia_megatons <= 0:
        return diametro_proy_m * 8, diametro_proy_m * 1.6
    
    energia_joules = energia_megatons * JOULES_PER_MEGATON
    
    if target_type == "water":
        densidad_objetivo = 1000
        resistencia_objetivo = 0.1e6
        K1, mu = 1.5, 0.55
    else:
        densidad_objetivo = 2500
        resistencia_objetivo = 1e7
        K1, mu = 1.2, 0.4
    
    angulo_rad = math.radians(angulo_impacto)
    correccion_angulo = math.sin(angulo_rad) ** 0.5
    
    diametro_transitorio = (
        K1 * 
        (densidad_proy / densidad_objetivo) ** (1/3) * 
        (energia_joules / resistencia_objetivo) ** (1/3) *
        correccion_angulo
    )
    
    diametro_final = 1.3 * diametro_transitorio
    profundidad_final = diametro_final * 0.22
    diametro_minimo = diametro_proy_m * 8
    diametro_final = max(diametro_final, diametro_minimo)
    
    return diametro_final, profundidad_final

def _fireball_radius_m(energia_megatons, altura_impacto_km=0):
    """
    Radio de la bola de fuego
    """
    if energia_megatons <= 0:
        return 0
    
    if altura_impacto_km > 5:
        coeficiente = 180
    else:
        coeficiente = 120
    
    radio_fireball = coeficiente * (energia_megatons ** (1/3))
    radio_fireball *= FIREBALL_SCALING_FACTOR
    
    return radio_fireball

def _thermal_radiation_radius_m(energia_megatons, tipo_lesion="lethal"):
    """
    Calcula radios para efectos de radiación térmica
    """
    if energia_megatons <= 0:
        return 0
    
    umbrales = {
        "lethal": 25,
        "burns_3rd": 15,
        "burns_2nd": 8,
        "burns_1st": 5,
        "ignition": 10
    }
    
    fluencia_objetivo = umbrales.get(tipo_lesion, 25)
    radio_termico = 450 * math.sqrt(energia_megatons) * (25/fluencia_objetivo)**0.5
    radio_termico *= THERMAL_SCALING_FACTOR
    
    return radio_termico

def _blast_overpressure_radii(energia_megatons, altura_impacto_km=0):
    """
    Calcula radios de sobrepresión
    """
    if energia_megatons <= 0:
        return {"50_psi": 0, "10_psi": 0, "5_psi": 0, "1_psi": 0}
    
    if altura_impacto_km > 2:
        factor_altura = 1.1 + (altura_impacto_km * 0.05)
    else:
        factor_altura = 1.0
    
    radio_base = 2500 * (energia_megatons ** (1/3)) * factor_altura
    radio_base *= BLAST_SCALING_FACTOR
    
    relaciones_presion = {
        "50_psi": 0.12,
        "10_psi": 0.30,
        "5_psi": 0.50,
        "1_psi": 1.0
    }
    
    return {psi: radio_base * factor for psi, factor in relaciones_presion.items()}

def _blast_wind_speed(overpressure_psi, energia_megatons):
    """
    Calcula velocidad del viento DINÁMICAMENTE basado en presión y energía
    """
    # Extraer valor PSI numérico primero
    if isinstance(overpressure_psi, str):
        psi_value = float(overpressure_psi.split('_')[0])
    else:
        psi_value = float(overpressure_psi)
    
    # Ahora verificar condiciones
    if psi_value <= 0 or energia_megatons <= 0:
        return 0
    
    # BASE: Velocidades máximas teóricas para diferentes niveles de PSI
    velocidades_base = {
        50: 2100,  # Máxima velocidad para destrucción total
        10: 800,   # Alta velocidad para colapso estructural
        5: 400,    # Velocidad moderada-alta para daño severo
        1: 160     # Velocidad baja-moderada para rotura de ventanas
    }
    
    # Obtener velocidad base para este nivel de PSI
    velocidad_base = velocidades_base.get(psi_value, 100)
    
    # FACTOR DE ENERGÍA: Ajustar según la energía del impacto
    # Para energías muy pequeñas (< 0.1 MT), reducir velocidad
    # Para energías muy grandes (> 100 MT), aumentar velocidad ligeramente
    if energia_megatons < 0.1:
        factor_energia = 0.3 + (energia_megatons / 0.1) * 0.7  # 30% a 100%
    elif energia_megatons > 100:
        factor_energia = 1.0 + min(0.5, (energia_megatons - 100) / 1000)  # 100% a 150%
    else:
        factor_energia = 1.0
    
    # Calcular velocidad final
    velocidad_final = velocidad_base * factor_energia
    
    return round(velocidad_final, 1)

def _estimate_seismic_magnitude(energia_joules, profundidad_km=0):
    """
    Calcula magnitud sísmica usando relación energía-magnitud
    """
    if energia_joules <= 0:
        return 0.0
    
    if profundidad_km > 1:
        eficiencia = 5e-4
    else:
        eficiencia = 1e-4
        
    energia_sismica = eficiencia * energia_joules
    
    if energia_sismica > 0:
        mw = (math.log10(energia_sismica) - 4.8) / 1.5
    else:
        mw = 0.0
    
    return round(max(mw, 0.0), 2)

def estimate_seismic_effects(mw, tipo_suelo="rock"):
    """
    Estima intensidades sísmicas
    """
    if mw >= 8.0:
        distances_km = [50, 200, 500, 1000, 2000, 5000]
    elif mw >= 7.0:
        distances_km = [30, 100, 300, 600, 1000, 2000]
    elif mw >= 6.0:
        distances_km = [20, 50, 150, 300, 600, 1000]
    elif mw >= 5.0:
        distances_km = [10, 30, 80, 150, 300, 500]
    elif mw >= 4.0:
        distances_km = [5, 15, 40, 80, 150, 250]
    else:
        distances_km = [2, 5, 10, 20, 40, 80]
    
    distances_km = [d for d in distances_km if d >= 1]
    
    if max(distances_km) < 5:
        distances_km.append(5)
    
    results = {}
    
    amplificacion_suelo = {
        "rock": 1.0,
        "hard_soil": 1.3,
        "soft_soil": 1.8,
        "sediment": 2.2
    }
    factor_suelo = amplificacion_suelo.get(tipo_suelo, 1.0)
    
    for d in distances_km:
        if d < 1:
            d = 1
        
        R = math.sqrt(d**2 + 10**2)
        log10_pga = (0.5 + 0.4 * mw - 1.0 * math.log10(R) - 0.002 * R)
        pga = (10 ** log10_pga) * factor_suelo
        
        if pga >= 1.0: mmi = "IX-X"
        elif pga >= 0.5: mmi = "VIII"
        elif pga >= 0.3: mmi = "VII"
        elif pga >= 0.15: mmi = "VI"
        elif pga >= 0.08: mmi = "V"
        elif pga >= 0.04: mmi = "IV"
        elif pga >= 0.02: mmi = "III"
        elif pga >= 0.01: mmi = "II"
        else: mmi = "I"
        
        results[f"{d}_km"] = {
            "pga_g": round(pga, 3),
            "mmi": mmi,
            "description": _get_mmi_description(mmi.split("-")[0])
        }
    
    return results

def _get_mmi_description(mmi):
    """Proporciona descripción detallada de intensidades MMI"""
    descripciones = {
        "I": "It doesn't feel",
        "II": "Felt by a few people at rest",
        "III": "Noticeably felt indoors",
        "IV": "Hanging objects sway, vibration like a passing truck",
        "V": "Felt by almost everyone, unstable objects fall",
        "VI": "Felt by all, minor damage to weak structures",
        "VII": "Minor damage to ordinary buildings",
        "VIII": "Considerable damage to ordinary structures",
        "IX": "General panic, significant damage to resistant structures",
        "X": "Destruction of most masonry structures"
    }
    return descripciones.get(mmi, "Unspecified intensity")

def _tsunami_effects(energia_megatons, profundidad_agua_m, distancia_costa_km):
    """
    Evalúa efectos de tsunami
    """
    if energia_megatons <= 1:
        return {"likely": False, "max_wave_height_m": 0, "notes": "Insufficient energy for significant tsunami"}
    
    if profundidad_agua_m > 1000:
        altura_onda = 0.02 * (energia_megatons ** 0.5)
    else:
        altura_onda = 0.01 * (energia_megatons ** 0.5)
    
    run_up_factor = 2.0
    altura_maxima_costa = altura_onda * run_up_factor
    
    if energia_megatons > 100:
        clasificacion = "Devastating regional tsunami"
    elif energia_megatons > 10:
        clasificacion = "Significant regional tsunami"
    elif energia_megatons > 1:
        clasificacion = "Local tsunami"
    else:
        clasificacion = "minor waves"
    
    return {
        "likely": energia_megatons > 1,
        "max_wave_height_m": round(altura_maxima_costa, 1),
        "classification": clasificacion,
        "notes": f"Estimated maximum height on the coast: {round(altura_maxima_costa, 1)} m"
    }

# ------------------ Endpoints ------------------

@require_http_methods(["GET", "OPTIONS"])
def asteroides(request):

    response = JsonResponse({"asteroides": []})
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    
    if request.method == "OPTIONS":
        response.status_code = 200
        return response
        
    hoy = datetime.date.today().strftime("%Y-%m-%d")
    manana = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    start_date = request.GET.get("start_date", hoy)
    end_date = request.GET.get("end_date", manana)

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "api_key": NASA_API_KEY
    }

    try:
        response = requests.get(NASA_NEO_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        asteroides_list = []
        for fecha, lista in data.get("near_earth_objects", {}).items():
            for ast in lista:
                asteroides_list.append({
                    "nombre": ast.get("name"),
                    "diametro_m": ast.get("estimated_diameter", {}).get("meters", {}).get("estimated_diameter_max"),
                    "velocidad_km_h": ast.get("close_approach_data", [])[0].get("relative_velocity", {}).get("kilometers_per_hour") if ast.get("close_approach_data") else None,
                    "fecha_aproximacion": ast.get("close_approach_data", [])[0].get("close_approach_date") if ast.get("close_approach_data") else None,
                    "distancia_km": ast.get("close_approach_data", [])[0].get("miss_distance", {}).get("kilometers") if ast.get("close_approach_data") else None,
                    "peligroso": ast.get("is_potentially_hazardous_asteroid"),
                })

        return JsonResponse({"asteroides": asteroides_list})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def impacto(request):
    nombre_asteroide = request.GET.get("asteroide", None)

    if nombre_asteroide:
        hoy = datetime.date.today().strftime("%Y-%m-%d")
        manana = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        params = {"start_date": hoy, "end_date": manana, "api_key": NASA_API_KEY}
        try:
            response = requests.get(NASA_NEO_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            encontrado = None
            for fecha, lista in data.get("near_earth_objects", {}).items():
                for ast in lista:
                    if ast.get("name", "").lower() == nombre_asteroide.lower():
                        encontrado = ast
                        break
                if encontrado:
                    break
            if not encontrado:
                return JsonResponse({"error": f"Asteroide '{nombre_asteroide}' no encontrado."}, status=404)

            diametro = encontrado.get("estimated_diameter", {}).get("meters", {}).get("estimated_diameter_max")
            velocidad_km_h = float(encontrado.get("close_approach_data", [])[0].get("relative_velocity", {}).get("kilometers_per_hour"))
            velocidad = velocidad_km_h * 1000 / 3600
            angulo = 45
            densidad = 3000
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    else:
        try:
            diametro = float(request.GET.get("diametro", 100))
            velocidad_input = float(request.GET.get("velocidad", 17))
            velocidad = velocidad_input * 1000
            angulo = float(request.GET.get("angulo", 45))
            densidad = float(request.GET.get("densidad", 3000))
        except Exception as e:
            return JsonResponse({"error": f"Parámetros inválidos: {e}"}, status=400)
        nombre_asteroide = "Custom input"

    # --- cálculos físicos ---
    masa, energia, velocidad_ms = _calculate_kinetic_energy(diametro, densidad, velocidad)
    energia_megatons = _energy_megatons(energia)

    # --- parámetros de ubicación ---
    lat = request.GET.get("lat", None)
    lon = request.GET.get("lon", None)
    target = request.GET.get("target", "land")
    altura_impacto = float(request.GET.get("altura", 0))

    # --- cálculos de efectos ---
    crater_diameter_m, crater_depth_m = _crater_metrics(
        diametro, energia_megatons, angulo, densidad, target
    )
    
    fireball_radius_m = _fireball_radius_m(energia_megatons, altura_impacto)
    
    thermal_effects_m = {
        "lethal": _thermal_radiation_radius_m(energia_megatons, "lethal"),
        "burns_3rd": _thermal_radiation_radius_m(energia_megatons, "burns_3rd"),
        "burns_2nd": _thermal_radiation_radius_m(energia_megatons, "burns_2nd"),
        "ignition": _thermal_radiation_radius_m(energia_megatons, "ignition")
    }
    
    blast_radii = _blast_overpressure_radii(energia_megatons, altura_impacto)
    
    # CORRECCIÓN: Velocidades de viento DINÁMICAS basadas en energía
    blast_winds = {
        "50_psi": {
            "wind_speed_kmh": _blast_wind_speed("50_psi", energia_megatons)
        },
        "10_psi": {
            "wind_speed_kmh": _blast_wind_speed("10_psi", energia_megatons)
        },
        "5_psi": {
            "wind_speed_kmh": _blast_wind_speed("5_psi", energia_megatons)
        },
        "1_psi": {
            "wind_speed_kmh": _blast_wind_speed("1_psi", energia_megatons)
        }
    }
    
    mw = _estimate_seismic_magnitude(energia, 0)
    seismic_effects = estimate_seismic_effects(mw)

    # --- efectos de tsunami ---
    tsunami_effects = {}
    if target == "water":
        profundidad_agua = float(request.GET.get("profundidad_agua", 1000))
        tsunami_effects = _tsunami_effects(energia_megatons, profundidad_agua, 0)

    # --- VERIFICACIÓN DE RELACIONES FÍSICAS ---
    relaciones_verificacion = {
        "crater_to_fireball": fireball_radius_m / (crater_diameter_m/2) if crater_diameter_m > 0 else 0,
        "crater_to_blast_1psi": blast_radii["1_psi"] / (crater_diameter_m/2) if crater_diameter_m > 0 else 0,
        "crater_to_thermal_lethal": thermal_effects_m["lethal"] / (crater_diameter_m/2) if crater_diameter_m > 0 else 0,
    }

    response = {
        "nombre": nombre_asteroide,
        "parametros_entrada": {
            "diametro_projectil_m": diametro,
            "velocidad_impacto_m_s": velocidad_ms,
            "angulo_impacto_grados": angulo,
            "densidad_kg_m3": densidad,
            "tipo_objetivo": target,
            "altura_impacto_km": altura_impacto
        },
        "energia": {
            "masa_kg": masa,
            "energia_joules": energia,
            "energia_megatones_TNT": round(energia_megatons, 2),
            "equivalente_bombas_hiroshima": round(energia_megatons / 0.015, 1)
        },
        "efectos_impacto": {
            "crater_diameter_m": round(crater_diameter_m, 1),
            "crater_depth_m": round(crater_depth_m, 1),
            "fireball_radius_m": round(fireball_radius_m, 1),
            "thermal_effects_m": {k: round(v, 1) for k, v in thermal_effects_m.items()},
            "blast_overpressure_radii_m": {k: round(v, 1) for k, v in blast_radii.items()},
            "blast_wind_effects": blast_winds
        },
        "efectos_sismicos": {
            "magnitud_momento_Mw": mw,
            "intensidades_regionales": seismic_effects
        },
        "efectos_tsunami": tsunami_effects if tsunami_effects else {"likely": False},
        "ubicacion": {
            "lat": lat,
            "lon": lon,
            "target": target
        },
        "factores_escala_aplicados": {
            "thermal_scaling": THERMAL_SCALING_FACTOR,
            "blast_scaling": BLAST_SCALING_FACTOR,
            "fireball_scaling": FIREBALL_SCALING_FACTOR,
            "nota": "Factores optimizados para relaciones físicas realistas"
        },
        "verificacion_relaciones": {
            "fireball_vs_crater_ratio": round(relaciones_verificacion["crater_to_fireball"], 1),
            "blast_1psi_vs_crater_ratio": round(relaciones_verificacion["crater_to_blast_1psi"], 1),
            "thermal_vs_crater_ratio": round(relaciones_verificacion["crater_to_thermal_lethal"], 1),
            "nota": "Ratios > 1 indican que el efecto es más extenso que el cráter"
        },
        "referencias_cientificas": [
            "Collins et al. (2005) - Earth Impact Effects Program",
            "Melosh (1989) - Impact Cratering: A Geologic Process", 
            "Glasstone & Dolan (1977) - The Effects of Nuclear Weapons",
            "Atkinson & Boore (2006) - Ground-motion prediction equations"
        ]
    }

    return JsonResponse(response)