#!/usr/bin/env python
"""
Amap MCP Server

This server implements the Model Context Protocol (MCP) for Amap APIs.
It wraps Amap's REST API endpoints and provides them as MCP tools.
"""

import os
import json
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment variables
load_dotenv()

# Get API key from environment
AMAP_API_KEY = os.getenv("MCP_PROJECT_KEY")
if not AMAP_API_KEY:
    raise ValueError("MCP_PROJECT_KEY environment variable not set")

# Create the MCP server
mcp = FastMCP("AmapTools")

# Base URL for Amap REST API
AMAP_BASE_URL = "https://restapi.amap.com/v3"

@mcp.tool()
def maps_geo(address: str, city: str = None) -> dict:
    """
    Convert an address to geographic coordinates (geocoding).
    
    Args:
        address: The address to geocode
        city: Optional city to narrow the search
    
    Returns:
        Geocoding result with coordinates
    """
    params = {
        "key": AMAP_API_KEY,
        "address": address,
    }
    
    if city:
        params["city"] = city
        
    url = f"{AMAP_BASE_URL}/geocode/geo"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_regeocode(location: str) -> dict:
    """
    Convert geographic coordinates to address (reverse geocoding).
    
    Args:
        location: Location in format "longitude,latitude"
    
    Returns:
        Address information for the coordinates
    """
    params = {
        "key": AMAP_API_KEY,
        "location": location,
    }
        
    url = f"{AMAP_BASE_URL}/geocode/regeo"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_direction_driving(origin: str, destination: str) -> dict:
    """
    Get driving directions between two points.
    
    Args:
        origin: Starting point in format "longitude,latitude"
        destination: Ending point in format "longitude,latitude"
    
    Returns:
        Driving directions information
    """
    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
        "extensions": "all"  # Return detailed route information
    }
        
    url = f"{AMAP_BASE_URL}/direction/driving"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_direction_walking(origin: str, destination: str) -> dict:
    """
    Get walking directions between two points.
    
    Args:
        origin: Starting point in format "longitude,latitude"
        destination: Ending point in format "longitude,latitude"
    
    Returns:
        Walking directions information
    """
    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
    }
        
    url = f"{AMAP_BASE_URL}/direction/walking"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_direction_transit(origin: str, destination: str, city: str, cityd: str = None) -> dict:
    """
    Get public transit directions between two points.
    
    Args:
        origin: Starting point in format "longitude,latitude"
        destination: Ending point in format "longitude,latitude"
        city: Starting city
        cityd: Destination city (if different from starting city)
    
    Returns:
        Transit directions information
    """
    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
        "city": city,
    }
    
    if cityd:
        params["cityd"] = cityd
        
    url = f"{AMAP_BASE_URL}/direction/transit/integrated"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_direction_bicycling(origin: str, destination: str) -> dict:
    """
    Get bicycling directions between two points.
    
    Args:
        origin: Starting point in format "longitude,latitude"
        destination: Ending point in format "longitude,latitude"
    
    Returns:
        Bicycling directions information
    """
    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
    }
        
    url = f"{AMAP_BASE_URL}/direction/bicycling"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_distance(origins: str, destination: str, type: str = "1") -> dict:
    """
    Calculate distance between points.
    
    Args:
        origins: Starting points in format "longitude1,latitude1;longitude2,latitude2"
        destination: Ending point in format "longitude,latitude"
        type: Distance type - 1 for driving, 0 for straight line, 3 for walking
    
    Returns:
        Distance calculation results
    """
    params = {
        "key": AMAP_API_KEY,
        "origins": origins,
        "destination": destination,
        "type": type,
    }
        
    url = f"{AMAP_BASE_URL}/distance"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_text_search(keywords: str, city: str = None, citylimit: bool = False) -> dict:
    """
    Search for POIs by keywords.
    
    Args:
        keywords: Search keywords
        city: Optional city to narrow the search
        citylimit: Whether to limit the search to the specified city
    
    Returns:
        POI search results
    """
    params = {
        "key": AMAP_API_KEY,
        "keywords": keywords,
    }
    
    if city:
        params["city"] = city
        
    if citylimit:
        params["citylimit"] = "true"
        
    url = f"{AMAP_BASE_URL}/place/text"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_around_search(keywords: str, location: str, radius: str = "1000") -> dict:
    """
    Search for POIs around a location.
    
    Args:
        keywords: Search keywords
        location: Center point in format "longitude,latitude"
        radius: Search radius in meters (default: 1000)
    
    Returns:
        POI search results
    """
    params = {
        "key": AMAP_API_KEY,
        "keywords": keywords,
        "location": location,
        "radius": radius,
    }
        
    url = f"{AMAP_BASE_URL}/place/around"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_search_detail(id: str) -> dict:
    """
    Get detailed information about a POI.
    
    Args:
        id: POI ID
    
    Returns:
        Detailed POI information
    """
    params = {
        "key": AMAP_API_KEY,
        "id": id,
    }
        
    url = f"{AMAP_BASE_URL}/place/detail"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_weather(city: str) -> dict:
    """
    Get weather information for a city.
    
    Args:
        city: City name or adcode
    
    Returns:
        Weather information
    """
    params = {
        "key": AMAP_API_KEY,
        "city": city,
        "extensions": "all",  # Return forecast and live weather
    }
        
    url = f"{AMAP_BASE_URL}/weather/weatherInfo"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

@mcp.tool()
def maps_ip_location(ip: str) -> dict:
    """
    Get location information for an IP address.
    
    Args:
        ip: IP address
    
    Returns:
        Location information for the IP
    """
    params = {
        "key": AMAP_API_KEY,
        "ip": ip,
    }
        
    url = f"{AMAP_BASE_URL}/ip"
    response = requests.get(url, params=params)
    response.raise_for_status()
    
    return response.json()

# Run the server when this file is executed
if __name__ == "__main__":
    print(f"Starting Amap MCP Server with API key: {AMAP_API_KEY[:5]}...")
    mcp.run() 