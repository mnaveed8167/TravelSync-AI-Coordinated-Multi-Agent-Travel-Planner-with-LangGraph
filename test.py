from Tools.tavily_tool import tavily_search
from Tools.flight_tool import search_flights
from backend import run_travel_agent
# res = tavily_search("Best Hotels In Pakistan?")

# print(res)

# res = search_flights("Plan a 7 days UAE trip from Islamabad, Pakistan")
# print(res)

user_input = input("Enter Travel Request:")

response = run_travel_agent(
    user_input= user_input,
    thread_id="test_user"
)

print("\n Final Response: \n")
print(response["answer"])