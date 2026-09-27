[out:json][timeout:180];
area["ISO3166-2"="US-GA"]["admin_level"="4"]->.search_state;
nwr["power"="substation"](area.search_state);
out geom;