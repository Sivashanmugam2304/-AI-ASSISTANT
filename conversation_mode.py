"""
Enhanced Conversation Mode for Fake AI Assistant
Provides natural, engaging conversations with context awareness,
emotion tracking, and intelligent responses.
"""

import time
import random
import re
from datetime import datetime

# Exit phrases
STOP_PHRASES = [
    "stop conversation", 
    "stop chatting", 
    "exit conversation", 
    "end chat", 
    "stop chat",
    "that's all",
    "goodbye conversation",
    "quit chatting"
]

# Categorized conversation prompts
CONVERSATION_PROMPTS = {
    'greeting': [
        "How has your day been so far?",
        "What's the best thing that happened to you today?",
        "How are you feeling right now?",
        "What's on your mind today?"
    ],
    'hobbies': [
        "What's a hobby you've been enjoying recently?",
        "Do you have any creative projects you're working on?",
        "What do you like to do in your free time?",
        "Have you picked up any new skills lately?"
    ],
    'entertainment': [
        "What's your favorite movie or show at the moment?",
        "Have you watched anything interesting lately?",
        "Do you have a favorite book or author?",
        "What kind of music do you enjoy?",
        "Any good podcasts you'd recommend?"
    ],
    'travel': [
        "If you could travel anywhere right now, where would you go?",
        "What's the most interesting place you've ever visited?",
        "Do you prefer mountains, beaches, or cities?",
        "Any dream vacation destinations on your list?"
    ],
    'food': [
        "What's your favorite food or cuisine?",
        "Have you tried any new restaurants recently?",
        "Do you enjoy cooking? What's your specialty?",
        "Coffee or tea person? Or neither?"
    ],
    'technology': [
        "What's your favorite piece of technology you own?",
        "Are you excited about any upcoming tech?",
        "Do you have any favorite apps or websites?",
        "What do you think about AI assistants like me?"
    ],
    'personal': [
        "What are you most proud of accomplishing?",
        "What motivates you to get up in the morning?",
        "If you could learn one new skill instantly, what would it be?",
        "What's something you're looking forward to?"
    ],
    'fun': [
        "Would you rather have the ability to fly or be invisible?",
        "If you could have dinner with any person, who would it be?",
        "What superpower would you choose if you could have one?",
        "Cats or dogs? Or are you Team Neither?"
    ]
}

# Context-aware responses
RESPONSE_TEMPLATES = {
    'positive': [
        "That sounds wonderful! {follow_up}",
        "I'm so glad to hear that! {follow_up}",
        "That's fantastic! {follow_up}",
        "How exciting! {follow_up}",
        "That's really great! {follow_up}"
    ],
    'negative': [
        "I'm sorry to hear that. {follow_up}",
        "That must be tough. {follow_up}",
        "I understand that can be difficult. {follow_up}",
        "That sounds challenging. {follow_up}"
    ],
    'neutral': [
        "Interesting! {follow_up}",
        "I see. {follow_up}",
        "That's good to know. {follow_up}",
        "Thanks for sharing that. {follow_up}"
    ],
    'curious': [
        "Tell me more about that!",
        "That sounds interesting. Can you elaborate?",
        "I'd love to hear more details about that.",
        "What made you interested in that?",
        "How did you get into that?"
    ]
}

# Follow-up questions based on keywords
FOLLOW_UPS = {
    'work': [
        "What do you do for work?",
        "How's work been treating you?",
        "Do you enjoy your job?"
    ],
    'family': [
        "Do you have a big family?",
        "Are you close with your family?",
        "Any fun family traditions?"
    ],
    'friend': [
        "Sounds like a great friend!",
        "How long have you known them?",
        "What do you like doing together?"
    ],
    'love': [
        "That's wonderful!",
        "What do you love most about it?",
        "Sounds like you're passionate about that!"
    ],
    'hate': [
        "What makes you feel that way?",
        "That must be frustrating.",
        "Is there anything that could make it better?"
    ],
    'happy': [
        "I'm glad you're feeling good!",
        "What's making you happy?",
        "That's wonderful to hear!"
    ],
    'sad': [
        "I'm here to listen if you want to talk about it.",
        "Sometimes it helps to share what's bothering you.",
        "I hope things get better soon."
    ],
    'excited': [
        "Your enthusiasm is contagious!",
        "What are you most excited about?",
        "Tell me all about it!"
    ],
    'tired': [
        "You should get some rest!",
        "Have you been getting enough sleep?",
        "Maybe take a break and relax a bit?"
    ],
    'music': [
        "What genre do you prefer?",
        "Any favorite artists?",
        "Have you been to any good concerts?"
    ],
    'movie': [
        "What kind of movies do you like?",
        "Have you seen anything good recently?",
        "Who's your favorite director or actor?"
    ],
    'book': [
        "What's your favorite book?",
        "Fiction or non-fiction?",
        "Any authors you'd recommend?"
    ],
    'game': [
        "What kind of games do you play?",
        "Any favorites you'd recommend?",
        "PC, console, or mobile?"
    ],
    'sport': [
        "Do you play sports or just watch?",
        "What's your favorite team?",
        "How often do you play or watch?"
    ]
}

# Sentiment keywords
POSITIVE_WORDS = ['good', 'great', 'awesome', 'wonderful', 'excellent', 'amazing', 
                  'fantastic', 'love', 'happy', 'excited', 'best', 'perfect', 'fun',
                  'enjoy', 'glad', 'nice', 'brilliant', 'beautiful', 'incredible']

NEGATIVE_WORDS = ['bad', 'terrible', 'awful', 'horrible', 'sad', 'angry', 'hate',
                  'upset', 'disappointed', 'frustrated', 'annoying', 'worst', 'boring',
                  'tired', 'sick', 'hurt', 'pain', 'difficult', 'hard', 'problem']


class ConversationContext:
    """Tracks conversation context and history."""
    
    def __init__(self):
        self.topics_discussed = set()
        self.sentiment_history = []
        self.last_category = None
        self.user_preferences = {}
        self.interaction_count = 0
        self.start_time = datetime.now()
    
    def add_topic(self, topic):
        """Add a discussed topic."""
        self.topics_discussed.add(topic)
    
    def add_sentiment(self, sentiment):
        """Track sentiment over conversation."""
        self.sentiment_history.append(sentiment)
        # Keep only last 5 sentiments
        if len(self.sentiment_history) > 5:
            self.sentiment_history.pop(0)
    
    def get_unused_category(self):
        """Get a conversation category that hasn't been used yet."""
        unused = [cat for cat in CONVERSATION_PROMPTS.keys() 
                  if cat not in self.topics_discussed]
        if unused:
            return random.choice(unused)
        # If all used, return random
        return random.choice(list(CONVERSATION_PROMPTS.keys()))
    
    def increment_interaction(self):
        """Track number of exchanges."""
        self.interaction_count += 1
    
    def get_duration(self):
        """Get conversation duration in minutes."""
        return (datetime.now() - self.start_time).seconds // 60


def analyze_sentiment(text):
    """
    Analyze sentiment of user's response.
    
    Args:
        text (str): User's input text
        
    Returns:
        str: 'positive', 'negative', or 'neutral'
    """
    if not text:
        return 'neutral'
    
    text_lower = text.lower()
    
    positive_count = sum(1 for word in POSITIVE_WORDS if word in text_lower)
    negative_count = sum(1 for word in NEGATIVE_WORDS if word in text_lower)
    
    # Exclamation marks indicate excitement
    if text.count('!') >= 2:
        positive_count += 1
    
    # All caps (excitement or anger - assume positive in conversation)
    if text.isupper() and len(text) > 5:
        positive_count += 1
    
    if positive_count > negative_count:
        return 'positive'
    elif negative_count > positive_count:
        return 'negative'
    else:
        return 'neutral'


def extract_keywords(text):
    """
    Extract important keywords from user response.
    
    Args:
        text (str): User's input
        
    Returns:
        list: List of detected keywords
    """
    text_lower = text.lower()
    detected = []
    
    for keyword in FOLLOW_UPS.keys():
        if keyword in text_lower:
            detected.append(keyword)
    
    return detected


def generate_response(user_input, context):
    """
    Generate contextually appropriate response.
    
    Args:
        user_input (str): User's message
        context (ConversationContext): Current conversation context
        
    Returns:
        str: Assistant's response
    """
    if not user_input or user_input.lower() == 'none':
        return "I didn't catch that. Could you say that again?"
    
    # Analyze sentiment
    sentiment = analyze_sentiment(user_input)
    context.add_sentiment(sentiment)
    
    # Extract keywords
    keywords = extract_keywords(user_input)
    
    # Special keyword responses
    if 'joke' in user_input.lower():
        jokes = [
            "Why did the programmer quit his job? Because he didn't get arrays!",
            "Why do programmers prefer dark mode? Because light attracts bugs!",
            "What's a computer's favorite snack? Microchips!",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself!",
            "How many programmers does it take to change a light bulb? None, that's a hardware problem!"
        ]
        return random.choice(jokes)
    
    if 'time' in user_input.lower() or 'date' in user_input.lower():
        return "Time flies when we're having a good conversation! But I'm just here to chat, not set alarms right now."
    
    if 'weather' in user_input.lower():
        return "I hope the weather is treating you well! But let's keep chatting about other things for now."
    
    # Build response based on sentiment
    if sentiment == 'positive':
        base_response = random.choice(RESPONSE_TEMPLATES['positive'])
    elif sentiment == 'negative':
        base_response = random.choice(RESPONSE_TEMPLATES['negative'])
    else:
        base_response = random.choice(RESPONSE_TEMPLATES['neutral'])
    
    # Add follow-up based on keywords
    follow_up = ""
    if keywords:
        keyword = random.choice(keywords)
        follow_up = random.choice(FOLLOW_UPS[keyword])
    else:
        follow_up = random.choice(RESPONSE_TEMPLATES['curious'])
    
    # Format response
    response = base_response.format(follow_up=follow_up)
    
    return response


def get_next_prompt(context):
    """
    Get next conversation prompt based on context.
    
    Args:
        context (ConversationContext): Current conversation context
        
    Returns:
        str: Next question/prompt
    """
    # Every 5 interactions, check in on user
    if context.interaction_count > 0 and context.interaction_count % 5 == 0:
        return "We've been chatting for a while! Are you enjoying our conversation?"
    
    # Every 8 interactions, offer to change topic
    if context.interaction_count > 0 and context.interaction_count % 8 == 0:
        return "Would you like to talk about something different, or should we keep going?"
    
    # Get a fresh category
    category = context.get_unused_category()
    context.add_topic(category)
    context.last_category = category
    
    # Select random prompt from category
    return random.choice(CONVERSATION_PROMPTS[category])


def start_conversation(speak, input_fn):
    """
    Run an enhanced conversational mode with context awareness.
    
    This mode creates a more natural, flowing conversation with:
    - Sentiment analysis
    - Context tracking
    - Varied responses
    - Follow-up questions
    - Topic diversity
    
    Args:
        speak (callable): Function to speak text
        input_fn (callable): Function to get user input
        
    Returns:
        None (exits when user requests)
    """
    context = ConversationContext()
    
    speak("Starting conversation mode! I'm here to have a nice chat with you.")
    speak("Say 'stop conversation' whenever you want to exit and get back to commands.")
    time.sleep(0.5)
    
    # Opening
    speak("So, tell me - how's your day going?")
    
    while True:
        # Get user response
        resp = input_fn()
        
        if not resp or resp.lower() == 'none':
            speak("I didn't hear you clearly. Want to try again?")
            time.sleep(0.3)
            continue
        
        lower_resp = resp.lower()
        
        # Check for exit phrases
        should_exit = False
        for phrase in STOP_PHRASES:
            if phrase in lower_resp:
                should_exit = True
                break
        
        if should_exit:
            duration = context.get_duration()
            if duration > 0:
                speak(f"We've been chatting for {duration} minutes! Thanks for the conversation.")
            else:
                speak("Thanks for chatting with me!")
            speak("Exiting conversation mode. Back to normal operations.")
            return
        
        # Track interaction
        context.increment_interaction()
        
        # Generate contextual response
        response = generate_response(resp, context)
        speak(response)
        time.sleep(0.5)
        
        # Ask next question
        next_prompt = get_next_prompt(context)
        speak(next_prompt)
        time.sleep(0.3)


# For testing
if __name__ == "__main__":
    def mock_speak(text):
        print(f"Assistant: {text}")
    
    def mock_input(prompt=None):
        return input("You: ")
    
    start_conversation(mock_speak, mock_input)