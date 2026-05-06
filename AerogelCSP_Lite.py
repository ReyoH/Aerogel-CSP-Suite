import tkinter as tk
from tkinter import messagebox, simpledialog
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import requests
import numpy as np
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from datetime import datetime
import webbrowser
import urllib.parse

# ---------- ГЕОЛОКАЦИЯ ----------
def get_location_by_ip():
    try:
        resp = requests.get('http://ip-api.com/json/?fields=city,lat,lon', timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                return data['city'], float(data['lat']), float(data['lon'])
    except:
        pass
    return "Новосибирск", 55.04, 82.93

def geocode_city(city_name):
    """Возвращает (широта, долгота) для названия города через Nominatim."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {'q': city_name, 'format': 'json', 'limit': 1}
    try:
        resp = requests.get(url, params=params, timeout=10, headers={'User-Agent': 'AerogelCSP/1.0'})
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
        return None, None
    except:
        return None, None

# ---------- NASA POWER API ----------
def fetch_data(lat, lon, start, end):
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        'parameters': 'T2M,RH2M,ALLSKY_SFC_SW_DWN',
        'community': 'RE',
        'longitude': lon,
        'latitude': lat,
        'start': start,
        'end': end,
        'format': 'JSON'
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 200:
        return resp.json()['properties']['parameter']
    else:
        raise ConnectionError(f"Ошибка API: {resp.status_code}")

def adsorption_capacity(T_day, RH):
    T_night = T_day - 5.0
    cap = 0.001 * RH * (1.2 - 0.02 * T_night)
    return max(0.0, min(0.8, cap))

def simulate_day(T_mean, RH_mean, solar_kwh):
    CONCENTRATOR_EFF = 0.70
    VACUUM_EVAP_TEMP = 45
    CP_WATER = 4.18
    LATENT_HEAT = 2260
    HEAT_REGEN = 0.6 * 3600
    SORBENT_MASS = 1.0

    Q_solar = solar_kwh * 3600.0
    Q_useful = Q_solar * CONCENTRATOR_EFF
    E_regen = HEAT_REGEN * SORBENT_MASS

    if Q_useful >= E_regen:
        regen_frac = 1.0
        Q_after = Q_useful - E_regen
    else:
        regen_frac = Q_useful / E_regen if E_regen > 0 else 0
        Q_after = 0.0

    dT = VACUUM_EVAP_TEMP - T_mean
    if dT < 0: dT = 0
    energy_per_kg = dT * CP_WATER + LATENT_HEAT
    desal_kg = Q_after / energy_per_kg if energy_per_kg > 0 and Q_after > 0 else 0.0

    ads_cap = adsorption_capacity(T_mean, RH_mean)
    water_from_sorb = ads_cap * SORBENT_MASS
    water_released = water_from_sorb * regen_frac
    total = (water_released + desal_kg) * 0.9
    return max(0.0, total)

CITIES = {
    "Выберите город": (None, None),
    "Москва": (55.75, 37.62),
    "Новосибирск": (55.04, 82.93),
    "Астрахань": (46.35, 48.04),
    "Дубай": (25.20, 55.27),
    "Краснодар": (44.50, 39.50),
    "Эр-Рияд": (24.71, 46.67),
}

class LiteApp(ttk.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Aerogel-CSP Suite v4.2 Lite")
        self.geometry("1000x750")
        self.minsize(800, 500)

        self.auto_city, self.auto_lat, self.auto_lon = get_location_by_ip()
        self.current_lat = self.auto_lat
        self.current_lon = self.auto_lon
        self.water_dates = None
        self.water_daily = None
        self.data_cache = {}
        self.auto_location_enabled = tk.BooleanVar(value=True)

        self.create_widgets()

    def create_widgets(self):
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Вкладка 1: Прогноз воды
        self.tab_water = ttk.Frame(nb)
        nb.add(self.tab_water, text="Прогноз воды")
        self.setup_water_tab()

        # Вкладка 2: Экономия ЦОД
        self.tab_dc = ttk.Frame(nb)
        nb.add(self.tab_dc, text="Экономия для ЦОД")
        self.setup_dc_tab()

        # Вкладка 3: Тепловой расчёт
        self.tab_thermal = ttk.Frame(nb)
        nb.add(self.tab_thermal, text="Тепловой расчёт")
        self.setup_thermal_tab()

        # Вкладка 4: Водный след ЦОД
        self.tab_footprint = ttk.Frame(nb)
        nb.add(self.tab_footprint, text="Водный след")
        self.setup_water_footprint_tab()

        # Строка состояния
        status_frame = ttk.Frame(self)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_text = tk.StringVar(value="Готов")
        ttk.Label(status_frame, textvariable=self.status_text, anchor=tk.W, relief=tk.SUNKEN,
                  font=('Segoe UI', 9), padding=5).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.theme_btn = ttk.Button(status_frame, text="Тёмная тема", command=self.toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=10, pady=2)

    # ---------- ВКЛАДКА ПРОГНОЗ ВОДЫ ----------
    def setup_water_tab(self):
        control_frame = ttk.Frame(self.tab_water, padding=10)
        control_frame.pack(fill=tk.X)

        # Локация
        loc_frame = ttk.Labelframe(control_frame, text="Местоположение", padding=10)
        loc_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0,10))

        ttk.Label(loc_frame, text="Город (список):").grid(row=0, column=0, sticky=tk.W)
        self.city_var = tk.StringVar()
        city_cb = ttk.Combobox(loc_frame, textvariable=self.city_var, values=list(CITIES.keys()), state="readonly", width=16)
        city_cb.grid(row=0, column=1, padx=5)
        city_cb.bind('<<ComboboxSelected>>', self.on_city_selected)

        # Новый блок: поиск по названию
        ttk.Label(loc_frame, text="Поиск города:").grid(row=1, column=0, sticky=tk.W, pady=(10,0))
        self.city_search_var = tk.StringVar()
        ttk.Entry(loc_frame, textvariable=self.city_search_var, width=16).grid(row=1, column=1, pady=(10,0))
        ttk.Button(loc_frame, text="Найти координаты", command=self.search_city).grid(row=2, column=0, columnspan=2, pady=5)

        self.auto_lbl = ttk.Label(loc_frame, text=f"IP: {self.auto_city} ({self.auto_lat:.2f}, {self.auto_lon:.2f})",
                                  font=('Segoe UI', 9, 'italic'))
        self.auto_lbl.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Button(loc_frame, text="Взять авто", command=self.use_auto_coords).grid(row=4, column=0, columnspan=2, pady=5)
        ttk.Checkbutton(loc_frame, text="Авто IP", variable=self.auto_location_enabled,
                        command=self.toggle_auto_location).grid(row=5, column=0, columnspan=2, pady=5, sticky=tk.W)

        # Параметры
        param_frame = ttk.Labelframe(control_frame, text="Параметры расчёта", padding=10)
        param_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(param_frame, text="Широта:").grid(row=0, column=0, sticky=tk.E)
        self.lat_var = tk.StringVar(value=str(self.auto_lat))
        ttk.Entry(param_frame, textvariable=self.lat_var, width=12).grid(row=0, column=1, padx=5)

        ttk.Label(param_frame, text="Долгота:").grid(row=0, column=2, sticky=tk.E, padx=(15,0))
        self.lon_var = tk.StringVar(value=str(self.auto_lon))
        ttk.Entry(param_frame, textvariable=self.lon_var, width=12).grid(row=0, column=3, padx=5)

        ttk.Label(param_frame, text="Начало (ГГГГММДД):").grid(row=1, column=0, sticky=tk.E, pady=10)
        self.start_var = tk.StringVar(value="20240101")
        ttk.Entry(param_frame, textvariable=self.start_var, width=12).grid(row=1, column=1, padx=5)

        ttk.Label(param_frame, text="Конец (ГГГГММДД):").grid(row=1, column=2, sticky=tk.E, pady=10, padx=(15,0))
        self.end_var = tk.StringVar(value="20241231")
        ttk.Entry(param_frame, textvariable=self.end_var, width=12).grid(row=1, column=3, padx=5)

        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(side=tk.LEFT, padx=20)
        self.calc_btn = ttk.Button(btn_frame, text="Рассчитать", command=self.start_water_calc, bootstyle="success", width=16)
        self.calc_btn.pack(pady=5)
        self.save_btn = ttk.Button(btn_frame, text="Сохранить PDF", command=self.save_pdf, state=tk.DISABLED)
        self.save_btn.pack(pady=5)
        self.save_img_btn = ttk.Button(btn_frame, text="Сохранить PNG", command=self.save_report, state=tk.DISABLED)
        self.save_img_btn.pack(pady=5)
        ttk.Button(btn_frame, text="Открыть карту", command=self.open_map_dialog).pack(pady=5)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(self.tab_water, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)

        cards_frame = ttk.Frame(self.tab_water)
        cards_frame.pack(fill=tk.X, padx=10, pady=(0,10))
        self.card_avg = ttk.Label(cards_frame, text="Среднее: —", font=('Segoe UI', 11, 'bold'), background='#e0e0e0', padding=8)
        self.card_avg.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.card_year = ttk.Label(cards_frame, text="Годовой сбор: —", font=('Segoe UI', 11, 'bold'), background='#c8e6c9', padding=8)
        self.card_year.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.card_max = ttk.Label(cards_frame, text="Макс. в день: —", font=('Segoe UI', 11, 'bold'), background='#ffe0b2', padding=8)
        self.card_max.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        self.fig_water = Figure(figsize=(8, 3.5), dpi=80)
        self.ax_water = self.fig_water.add_subplot(111)
        self.canvas_water = FigureCanvasTkAgg(self.fig_water, self.tab_water)
        self.canvas_water.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(self.tab_water, height=4, bg='white', wrap=tk.WORD)
        scroll = ttk.Scrollbar(self.tab_water, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

    # ---------- ВКЛАДКА ЭКОНОМИЯ ЦОД ----------
    def setup_dc_tab(self):
        frame = ttk.Labelframe(self.tab_dc, text="Параметры системы охлаждения", padding=20)
        frame.pack(fill=tk.X, padx=20, pady=20)

        ttk.Label(frame, text="Тепловая мощность стойки (кВт):").grid(row=0, column=0, sticky=tk.E, pady=5)
        self.power_var = tk.StringVar(value="40")
        ttk.Entry(frame, textvariable=self.power_var, width=15).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="PUE водяного:").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.pue_var = tk.StringVar(value="1.15")
        ttk.Entry(frame, textvariable=self.pue_var, width=15).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="Стоимость воды (руб./м³):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.water_cost_var = tk.StringVar(value="50")
        ttk.Entry(frame, textvariable=self.water_cost_var, width=15).grid(row=2, column=1, padx=5)

        ttk.Button(frame, text="Рассчитать экономию", command=self.calc_datacenter).grid(row=3, column=0, columnspan=2, pady=10)
        self.dc_result = ttk.Label(frame, text="Результат появится здесь...", font=('Segoe UI', 11, 'italic'))
        self.dc_result.grid(row=4, column=0, columnspan=2)

    # ---------- ВКЛАДКА ТЕПЛОВОЙ РАСЧЁТ ----------
    def setup_thermal_tab(self):
        frame = ttk.Labelframe(self.tab_thermal, text="Микроканальный испаритель (Novec 7000)", padding=20)
        frame.pack(fill=tk.X, padx=20, pady=20)

        ttk.Label(frame, text="Мощность чипа (Вт):").grid(row=0, column=0, sticky=tk.E, pady=5)
        self.chip_power = tk.StringVar(value="1500")
        ttk.Entry(frame, textvariable=self.chip_power, width=15).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="Температура чипа (°C):").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.T_chip = tk.StringVar(value="85")
        ttk.Entry(frame, textvariable=self.T_chip, width=15).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="Расход хладагента (л/мин):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.flow_rate = tk.StringVar(value="0.5")
        ttk.Entry(frame, textvariable=self.flow_rate, width=15).grid(row=2, column=1, padx=5)

        ttk.Button(frame, text="Вычислить теплообмен", command=self.calc_thermal).grid(row=3, column=0, columnspan=2, pady=10)
        self.therm_result = ttk.Label(frame, text="Результат появится здесь...", font=('Segoe UI', 11))
        self.therm_result.grid(row=4, column=0, columnspan=2)

    # ---------- ВКЛАДКА ВОДНЫЙ СЛЕД ----------
    def setup_water_footprint_tab(self):
        frame = ttk.Labelframe(self.tab_footprint, text="Калькулятор водного следа ЦОД", padding=20)
        frame.pack(fill=tk.X, padx=20, pady=20)

        ttk.Label(frame, text="Общая мощность ЦОД (МВт):").grid(row=0, column=0, sticky=tk.E, pady=5)
        self.dc_mw_var = tk.StringVar(value="10")
        ttk.Entry(frame, textvariable=self.dc_mw_var, width=15).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="Годовое потребление электричества (ГВт·ч):").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.dc_gwh_var = tk.StringVar(value="87.6")
        ttk.Entry(frame, textvariable=self.dc_gwh_var, width=15).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="Текущее водопотребление (м³/год):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.current_water_var = tk.StringVar(value="500000")
        ttk.Entry(frame, textvariable=self.current_water_var, width=15).grid(row=2, column=1, padx=5)

        ttk.Button(frame, text="Рассчитать", command=self.calc_water_footprint).grid(row=3, column=0, columnspan=2, pady=10)
        self.footprint_result = ttk.Label(frame, text="Результат будет здесь...", font=('Segoe UI', 11, 'italic'))
        self.footprint_result.grid(row=4, column=0, columnspan=2)

    def calc_water_footprint(self):
        try:
            mw = float(self.dc_mw_var.get())
            gwh = float(self.dc_gwh_var.get())
            cur = float(self.current_water_var.get())
        except:
            messagebox.showerror("Ошибка", "Введите числовые значения")
            return
        # Традиционное водопотребление ~1.8 л/кВт·ч (среднее по отрасли)
        trad_water = gwh * 1_000_000 * 1.8  # в литрах
        trad_m3 = trad_water / 1000
        # Наша система сокращает до 0 (замкнутый цикл) + генерирует
        saved = trad_m3 + cur  # плюс текущее потребление, которое замещается
        txt = (f"Традиционное потребление: {trad_m3:,.0f} м³/год\n"
               f"Текущее потребление: {cur:,.0f} м³/год\n"
               f"Aerogel-CSP: экономит {saved:,.0f} м³/год\n"
               f"Эквивалентно снабжению водой города с населением ~{saved/200:.0f} чел.")
        self.footprint_result.config(text=txt)

    # ---------- ПОИСК КООРДИНАТ ПО НАЗВАНИЮ ----------
    def search_city(self):
        city = self.city_search_var.get().strip()
        if not city:
            messagebox.showwarning("Внимание", "Введите название города")
            return
        lat, lon = geocode_city(city)
        if lat is not None:
            self.lat_var.set(str(lat))
            self.lon_var.set(str(lon))
            messagebox.showinfo("Найдено", f"{city}: {lat:.4f}, {lon:.4f}")
        else:
            messagebox.showerror("Не найдено", "Город не найден, попробуйте другое написание")

    # ---------- ОТКРЫТИЕ КАРТЫ ----------
    def open_map_dialog(self):
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
        except:
            messagebox.showerror("Ошибка", "Некорректные координаты")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Открыть карту")
        dialog.geometry("230x170")
        dialog.resizable(False, False)
        ttk.Label(dialog, text="Выберите сервис:", font=('Segoe UI', 10)).pack(pady=10)
        def open_url(url):
            webbrowser.open(url)
            dialog.destroy()
        ttk.Button(dialog, text="OpenStreetMap", command=lambda: open_url(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=10/{lat}/{lon}")).pack(pady=3)
        ttk.Button(dialog, text="Яндекс.Карты", command=lambda: open_url(f"https://yandex.ru/maps/?ll={lon},{lat}&z=10")).pack(pady=3)
        ttk.Button(dialog, text="2ГИС", command=lambda: open_url(f"https://2gis.ru/routeSearch/rsType/car/geo/{lon},{lat}")).pack(pady=3)
        ttk.Button(dialog, text="Отмена", command=dialog.destroy).pack(pady=5)

    # ---------- ЛОГИКА ВОДЫ ----------
    def toggle_auto_location(self):
        if self.auto_location_enabled.get():
            self.use_auto_coords()

    def use_auto_coords(self):
        self.lat_var.set(str(self.auto_lat))
        self.lon_var.set(str(self.auto_lon))
        self.city_var.set('')

    def on_city_selected(self, event=None):
        city = self.city_var.get()
        if city in CITIES and CITIES[city][0] is not None:
            self.lat_var.set(str(CITIES[city][0]))
            self.lon_var.set(str(CITIES[city][1]))

    def toggle_theme(self):
        if self.style.theme_use() == "flatly":
            self.style.theme_use("darkly")
            self.theme_btn.config(text="Светлая тема")
            self.log_text.configure(bg='#2b3e4d', fg='white')
        else:
            self.style.theme_use("flatly")
            self.theme_btn.config(text="Тёмная тема")
            self.log_text.configure(bg='white', fg='black')

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.update_idletasks()

    def start_water_calc(self):
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
            start = self.start_var.get()
            end = self.end_var.get()
            if len(start) != 8 or len(end) != 8:
                raise ValueError
        except:
            messagebox.showerror("Ошибка", "Проверьте координаты и даты")
            return

        self.current_lat, self.current_lon = lat, lon
        self.calc_btn.config(state=tk.DISABLED)
        self.save_btn.config(state=tk.DISABLED)
        self.save_img_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.log_text.delete('1.0', tk.END)
        self.card_avg.config(text="Среднее: —")
        self.card_year.config(text="Годовой сбор: —")
        self.card_max.config(text="Макс. в день: —")

        self.after(50, self._run_model_step1, lat, lon, start, end)

    def _run_model_step1(self, lat, lon, start, end):
        cache_key = (round(lat, 2), round(lon, 2), start, end)
        if cache_key in self.data_cache:
            self.log("Использую кэш...")
            T, RH, SW, dates = self.data_cache[cache_key]
            self.after(10, self._run_model_step2, lat, lon, dates, T, RH, SW)
        else:
            self.log("Загрузка NASA...")
            self.progress_var.set(10)
            self.update_idletasks()
            self.after(50, self._fetch_and_continue, lat, lon, start, end, cache_key)

    def _fetch_and_continue(self, lat, lon, start, end, cache_key):
        try:
            params = fetch_data(lat, lon, start, end)
            T = params['T2M']
            RH = params['RH2M']
            SW = params['ALLSKY_SFC_SW_DWN']
            dates = sorted(T.keys())
            self.data_cache[cache_key] = (T, RH, SW, dates)
            self._run_model_step2(lat, lon, dates, T, RH, SW)
        except Exception as e:
            self.log(f"Ошибка: {e}")
            messagebox.showerror("Ошибка", str(e))
            self.calc_btn.config(state=tk.NORMAL)

    def _run_model_step2(self, lat, lon, dates, T, RH, SW):
        daily = []
        total_days = len(dates)
        for i, d in enumerate(dates):
            w = simulate_day(T[d], RH[d], SW[d])
            daily.append(w)
            if i % 50 == 0:
                self.progress_var.set(10 + 80 * i / total_days)
                self.update_idletasks()
        self.water_dates = dates
        self.water_daily = np.array(daily)
        avg = np.mean(self.water_daily)
        total = np.sum(self.water_daily)
        max_day = np.max(self.water_daily)

        self.card_avg.config(text=f"Среднее: {avg:.3f} л/д")
        self.card_year.config(text=f"Год: {total:.0f} л")
        self.card_max.config(text=f"Макс: {max_day:.3f} л")
        self.log(f"\nРезультаты ({lat:.2f}, {lon:.2f})")
        self.log(f"   Среднесуточно: {avg:.3f} литров/день")
        self.log(f"   Годовой сбор:  {total:.0f} литров")
        self.log(f"   Максимум за день: {max_day:.3f} литров")

        self.ax_water.clear()
        date_objs = [datetime.strptime(d, '%Y%m%d') for d in dates]
        self.ax_water.plot(date_objs, daily, color='#2a9d8f', lw=1.5)
        self.ax_water.set_title("Суточная производительность Aerogel-CSP", fontsize=10)
        self.ax_water.set_xlabel("Дата")
        self.ax_water.set_ylabel("Вода, литров/день")
        self.ax_water.grid(alpha=0.2)
        import matplotlib.dates as mdates
        self.ax_water.xaxis.set_major_locator(mdates.MonthLocator(bymonthday=1))
        self.ax_water.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        self.fig_water.autofmt_xdate(rotation=30)
        self.fig_water.tight_layout()
        self.canvas_water.draw()

        self.progress_var.set(100)
        self.calc_btn.config(state=tk.NORMAL)
        self.save_btn.config(state=tk.NORMAL)
        self.save_img_btn.config(state=tk.NORMAL)

    # ---------- СОХРАНЕНИЕ PNG ----------
    def save_report(self):
        if self.water_dates is None:
            messagebox.showwarning("Предупреждение", "Сначала выполните расчёт")
            return
        try:
            img_name = f"forecast_{self.current_lat:.2f}_{self.current_lon:.2f}.png"
            self.fig_water.savefig(img_name, dpi=100)
            messagebox.showinfo("Сохранено", f"График сохранён как {img_name}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    # ---------- СОХРАНЕНИЕ PDF ----------
    def save_pdf(self):
        if self.water_dates is None:
            messagebox.showwarning("Предупреждение", "Сначала выполните расчёт")
            return
        try:
            pdf_name = f"report_{self.current_lat:.2f}_{self.current_lon:.2f}.pdf"
            with PdfPages(pdf_name) as pdf:
                # Первая страница: график
                pdf.savefig(self.fig_water)
                # Вторая страница: текст
                fig_text = Figure(figsize=(8.27, 11.69))  # A4
                ax = fig_text.add_subplot(111)
                ax.axis('off')
                text = (f"Aerogel-CSP Suite v4.2 Отчёт\n\n"
                        f"Местоположение: {self.current_lat:.2f}, {self.current_lon:.2f}\n"
                        f"Период: {self.start_var.get()} — {self.end_var.get()}\n"
                        f"Среднесуточно: {np.mean(self.water_daily):.3f} л/день\n"
                        f"Годовой сбор: {np.sum(self.water_daily):.0f} л\n"
                        f"Максимум за день: {np.max(self.water_daily):.3f} л\n")
                ax.text(0.1, 0.8, text, fontsize=12, verticalalignment='top')
                pdf.savefig(fig_text)
            messagebox.showinfo("PDF сохранён", f"Отчёт: {pdf_name}")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения PDF", str(e))

    # ---------- РАСЧЁТЫ ----------
    def calc_datacenter(self):
        try:
            power = float(self.power_var.get())
            pue = float(self.pue_var.get())
            cost = float(self.water_cost_var.get())
        except:
            messagebox.showerror("Ошибка", "Введите числа")
            return
        water_m3_per_hour = power * 0.002
        water_year = water_m3_per_hour * 24 * 365
        cost_trad = water_year * cost
        water_gen_m3 = 3 * 365 / 1000
        saved = water_year + water_gen_m3
        money_saved = saved * cost
        txt = (f"Традиционное потребление: {water_year:.1f} м³/год\n"
               f"Затраты на воду: {cost_trad:,.0f} руб/год\n"
               f"Aerogel-CSP: экономит {water_year:.1f} + генерирует {water_gen_m3:.1f} м³\n"
               f"Общая экономия: {money_saved:,.0f} руб/год")
        self.dc_result.config(text=txt)

    def calc_thermal(self):
        try:
            Q = float(self.chip_power.get())
            T_chip = float(self.T_chip.get())
            flow = float(self.flow_rate.get())
        except:
            messagebox.showerror("Ошибка", "Введите числа")
            return
        rho = 1400.0; cp = 1100.0; nu = 0.4e-6; k = 0.075; Pr = 10.0; D_h = 0.0005
        A_channel = 100 * D_h**2
        velocity = (flow * 0.001 / 60) / A_channel if A_channel > 0 else 0
        Re = velocity * D_h / nu
        Nu = 4.36 if Re < 2300 else 0.023 * Re**0.8 * Pr**0.4
        h = Nu * k / D_h
        A_ht = 0.01
        R_conv = 1 / (h * A_ht) if h > 0 else float('inf')
        T_fluid = 30.0
        actual_dT = Q / (h * A_ht) if h > 0 else 0
        T_chip_calc = T_fluid + actual_dT
        txt = (f"Re = {Re:.1f}, Nu = {Nu:.2f}, h = {h:.0f} Вт/м²·К\n"
               f"Тепловое сопротивление: {R_conv:.4f} К/Вт\n"
               f"Ожидаемая температура чипа: {T_chip_calc:.1f} °C")
        self.therm_result.config(text=txt)

if __name__ == "__main__":
    app = LiteApp()
    app.mainloop()