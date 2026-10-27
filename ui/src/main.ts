import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';

const target = document.getElementById('app');
if (target === null) {
  throw new Error('mount target #app not found');
}

mount(App, { target });
