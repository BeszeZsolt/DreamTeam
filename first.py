import numpy as np
import pandas as pd
import streamlit as st

st.title("Streamlit Test UI")

st.write("Welcome to this basic Streamlit application!")

name = st.text_input("Enter your name:")
if name:
    st.write(f"Hello, {name}!")

age = st.slider("Select your age:", 0, 100, 25)
st.write(f"You are {age} years old")

option = st.selectbox("Choose an option:", ["Option 1", "Option 2", "Option 3"])
st.write(f"You selected: {option}")

if st.button("Click me!"):
    st.success("Button clicked!")

data = pd.DataFrame({
    "Name": ["Alice", "Bob", "Charlie"],
    "Age": [25, 30, 35],
    "Score": [85, 92, 78]
})
st.write("Sample Data:")
st.dataframe(data)