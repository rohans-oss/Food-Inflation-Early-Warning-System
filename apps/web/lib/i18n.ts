// UI labels in English and Kannada for the farmer-facing screens (V1). Alert texts are localised server-side.
// NOTE: the Kannada strings are machine-drafted and NOT yet reviewed by a native speaker (same status as
// services/api/agripulse_api/alerts.py, KN_REVIEWED = False). Get them checked before a field test.
export const KN_REVIEWED = false;

const en = {
  myLots: "My lots",
  newLot: "Register a harvest lot",
  crop: "Crop",
  quantity: "Quantity (tons)",
  grade: "Grade",
  pickup: "Pickup location",
  useMyLocation: "Use my location",
  save: "Save lot",
  todaysPrices: "Today's prices near you",
  forecast: "Price range, next 1–4 weeks",
  bestMandi: "Best mandi for this lot",
  liveVehicle: "Your vehicle",
  eta: "Arriving",
  delivered: "Delivered",
  alerts: "Alerts",
  logout: "Log out",
  spikeRisk: "Spike risk (14 days)",
  netValue: "Expected net value",
  distance: "Road distance",
  noLots: "No lots yet. Register your first harvest above.",
};

const kn: typeof en = {
  myLots: "ನನ್ನ ಲಾಟ್‌ಗಳು",
  newLot: "ಕೊಯ್ಲು ಲಾಟ್ ನೋಂದಾಯಿಸಿ",
  crop: "ಬೆಳೆ",
  quantity: "ಪ್ರಮಾಣ (ಟನ್)",
  grade: "ದರ್ಜೆ",
  pickup: "ಸಂಗ್ರಹ ಸ್ಥಳ",
  useMyLocation: "ನನ್ನ ಸ್ಥಳ ಬಳಸಿ",
  save: "ಲಾಟ್ ಉಳಿಸಿ",
  todaysPrices: "ಹತ್ತಿರದ ಮಂಡಿಗಳಲ್ಲಿ ಇಂದಿನ ಬೆಲೆ",
  forecast: "ಮುಂದಿನ 1–4 ವಾರಗಳ ಬೆಲೆ ಶ್ರೇಣಿ",
  bestMandi: "ಈ ಲಾಟ್‌ಗೆ ಉತ್ತಮ ಮಂಡಿ",
  liveVehicle: "ನಿಮ್ಮ ವಾಹನ",
  eta: "ತಲುಪುವ ಸಮಯ",
  delivered: "ತಲುಪಿಸಲಾಗಿದೆ",
  alerts: "ಎಚ್ಚರಿಕೆಗಳು",
  logout: "ನಿರ್ಗಮಿಸಿ",
  spikeRisk: "ಬೆಲೆ ಏರಿಕೆ ಸಾಧ್ಯತೆ (14 ದಿನ)",
  netValue: "ನಿರೀಕ್ಷಿತ ನಿವ್ವಳ ಮೌಲ್ಯ",
  distance: "ರಸ್ತೆ ದೂರ",
  noLots: "ಇನ್ನೂ ಲಾಟ್‌ಗಳಿಲ್ಲ. ಮೇಲೆ ನಿಮ್ಮ ಮೊದಲ ಕೊಯ್ಲು ನೋಂದಾಯಿಸಿ.",
};

export type Lang = "en" | "kn";
export type Key = keyof typeof en;

export function t(lang: Lang | undefined, key: Key): string {
  return (lang === "kn" ? kn : en)[key];
}
