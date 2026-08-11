from Tools.tavily_tool import tavily_search
from Tools.flight_tool import search_flights

# res = tavily_search("Best Hotels In Pakistan?")

# print(res)

res = search_flights("Plan a 7 days UAE trip from Islamabad, Pakistan")
print(res)