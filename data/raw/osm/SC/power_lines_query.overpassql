[out:json][timeout:180];
area["ISO3166-2"="US-SC"]["admin_level"="4"]->.search_state;
nwr["power"="line"](area.search_state);
out geom;